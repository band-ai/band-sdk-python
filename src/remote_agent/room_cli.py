"""Shell client for standalone rooms; private, identity-scoped durable state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

from remote_agent.capabilities import (
    UnsupportedHostError,
    exclusive_lock,
    require_linux,
    require_runtime,
)
from remote_agent.graphemes import count as grapheme_count
from remote_agent.network import HTTPRoom, strict_json
from remote_agent.output import write_line
from remote_agent.receiptwake import WakeJournal
from remote_agent.storage import StoreError, canonical
from remote_agent.transport import TransportError
from remote_agent.types import JsonObject, SourcePath


class ClientError(Exception):
    """Only credential-free diagnostics may cross the shell boundary."""


class PreflightError(ClientError):
    def __init__(
        self, field: str, constraint: str, actual: int, bounds: dict[str, int]
    ) -> None:
        super().__init__("Field length is outside the contract definition")
        self.detail = {
            "field": field,
            "constraint": constraint,
            "actual": actual,
            "min": bounds["min"],
            "max": bounds["max"],
        }


def definition_for(client: HTTPRoom) -> JsonObject:
    try:
        result = client._request("GET", "/v1/definition")
    except TransportError as error:
        if str(error) != "Room HTTP status 404":
            raise
        view = client._request("POST", "/v1/view", {})
        result = {
            "session": client.session,
            "actor": client.actor,
            "identity": client.identity,
            "definition": view["view"]["definition"],
            "profile": "contract-room/definition/1",
        }
    if (
        result.get("profile") != "contract-room/definition/1"
        or result.get("session") != client.session
        or result.get("actor") != client.actor
        or result.get("identity") != client.identity
        or not isinstance(result.get("definition"), dict)
    ):
        raise ClientError(
            "Room definition differs from the authenticated session identity"
        )
    return result


def preflight(client: HTTPRoom, kind: str, payload: JsonObject) -> None:
    definition = definition_for(client)["definition"]
    fields = definition.get("types", {}).get(kind, {}).get("fields", {})
    for field, schema in fields.items():
        value = payload.get(field)
        # Other payload validation remains the room's job; this is a length
        # preflight, never a second admission checker or a legal-move promise.
        if schema.get("kind") != "text" or not isinstance(value, str):
            continue
        for constraint in ("bytes", "graphemes"):
            bounds = schema.get(constraint)
            if bounds is None:
                continue
            if (
                not isinstance(bounds, dict)
                or type(bounds.get("min")) is not int
                or type(bounds.get("max")) is not int
                or not 0 <= bounds["min"] <= bounds["max"] <= 9_007_199_254_740_991
            ):
                raise ClientError("Invalid text bounds in room definition")
            actual = (
                len(value.encode("utf-8"))
                if constraint == "bytes"
                else grapheme_count(value)
            )
            if not bounds["min"] <= actual <= bounds["max"]:
                raise PreflightError(field, constraint, actual, bounds)


def rejection_hint(result: JsonObject, client: HTTPRoom) -> JsonObject:
    if result.get("status") != "rejected" or result.get("reason") != "illegal_message":
        return result
    try:
        # Read only the authenticated actor's current compact view. A hint is
        # advisory: another proposal or a timer can change legality immediately.
        projection = client._request("POST", "/v1/view", {})
        legal = projection["view"]["legal"]
        if not isinstance(legal, list) or any(
            not isinstance(rule, dict) or not isinstance(rule.get("kind"), str)
            for rule in legal
        ):
            raise ClientError("Invalid legal-move hint")
        kinds = list(dict.fromkeys(rule["kind"] for rule in legal))
        return dict(result, legal_kinds=kinds)
    except (TransportError, ClientError, ValueError, KeyError, TypeError):
        # Rejection is already definitive and persisted. An optional lookup
        # failure must neither mask it nor make the envelope look ambiguous.
        return dict(result, legal_kinds_unavailable=True)


def private_descriptor(path: SourcePath, flags: int = os.O_RDONLY) -> int:
    require_linux()
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        os.close(descriptor)
        raise ClientError("Private files must be owned regular files with mode 0600")
    return descriptor


def private_read(path: SourcePath) -> bytes:
    with os.fdopen(private_descriptor(path), "rb") as handle:
        encoded = handle.read(4_194_305)
    if len(encoded) > 4_194_304:
        raise ClientError("Private file exceeds capacity")
    return encoded


def private_directory(path: Path) -> None:
    require_linux()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ClientError("State directory must be owned and private (0700)")


def save(path: Path, value: JsonObject) -> None:
    """Atomic replace with file AND directory fsync before returning."""
    encoded = canonical(value)
    if path.exists():
        descriptor = private_descriptor(path)
        os.close(descriptor)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load(path: Path) -> JsonObject | None:
    if not path.exists():
        return None
    return strict_json(private_read(path))


@contextmanager
def state_lock(root: Path, scope: JsonObject) -> Generator[Path, None, None]:
    private_directory(root)
    directory = root / hashlib.sha256(canonical(scope)).hexdigest()
    private_directory(directory)
    descriptor = private_descriptor(directory / "state.lock", os.O_RDWR | os.O_CREAT)
    try:
        exclusive_lock(descriptor)
        yield directory
    finally:
        os.close(descriptor)


def scope_for(client: HTTPRoom) -> JsonObject:
    return {
        "endpoint": client.url,
        "actor": client.actor,
        "session": client.session,
        "identity": client.identity,
    }


def checked_record(record: JsonObject | None, scope: JsonObject) -> JsonObject | None:
    if record is not None and record.get("scope") != scope:
        raise ClientError("Saved state belongs to another room identity")
    return record


def proposal(
    args: argparse.Namespace, client: HTTPRoom, directory: Path, scope: JsonObject
) -> tuple[JsonObject, int]:
    path = directory / "staged.json"
    staged = checked_record(load(path), scope)
    if args.retry:
        if (
            args.kind is not None
            or args.payload is not None
            or args.expected_version is not None
        ):
            raise ClientError("Exact retry takes no kind, payload or expected version")
        if staged is None:
            raise ClientError("No staged proposal for this endpoint and actor")
        message = staged["envelope"]
    else:
        if args.kind is None or args.payload is None:
            raise ClientError(
                "A new proposal requires kind and JSON payload or payload-file path"
            )
        if staged is not None and not staged["resolved"]:
            raise ClientError(
                "Previous outcome is unknown; use propose --retry before a new proposal"
            )
        raw = args.payload
        if not raw.lstrip().startswith("{"):
            raw = Path(raw).read_bytes()
        payload = strict_json(raw)
        preflight(client, args.kind, payload)
        message = {
            "id": secrets.token_hex(16),
            "session": client.session,
            "kind": args.kind,
            "payload": payload,
        }
        if args.expected_version is not None:
            if args.expected_version < 0:
                raise ClientError("Expected version must be nonnegative")
            message["expected_version"] = args.expected_version
        staged = {"scope": scope, "envelope": message, "resolved": False}
        # The canonical envelope is retained before dispatch; HTTPRoom emits the
        # same canonical bytes on every invocation, including after a restart.
        save(path, staged)
    result = client.publish(message, client.actor)
    status = result.get("status")
    if status not in ("accepted", "duplicate", "rejected"):
        raise ClientError(
            "Unknown proposal outcome; staged envelope retained for reconciliation"
        )
    if status in ("accepted", "duplicate"):
        receipt = result.get("original", result)
        if receipt.get("id") != message["id"]:
            raise ClientError("Room receipt does not match the staged proposal")
        with WakeJournal(directory / "wakes.sqlite3", scope) as journal:
            journal.stage(result)
    save(path, dict(staged, resolved=True))
    return rejection_hint(result, client), 0 if status in (
        "accepted",
        "duplicate",
    ) else 1


def inbox(
    args: argparse.Namespace, client: HTTPRoom, directory: Path, scope: JsonObject
) -> None:
    path = directory / "cursor.json"
    saved = checked_record(load(path), scope)
    after = saved["cursor"] if saved else 0
    if type(after) is not int or not 0 <= after <= 9_007_199_254_740_991:
        raise ClientError("Saved cursor is invalid; explicit reconciliation required")
    offered_path = directory / "inbox-page.json"
    if args.ack is not None:
        cursor = args.ack
        if not after <= cursor <= 9_007_199_254_740_991:
            raise ClientError("Acknowledgement cursor is invalid or moves backwards")
        offered = checked_record(load(offered_path), scope)
        if cursor != after and (
            offered is None
            or offered.get("after") != after
            or offered.get("cursor") != cursor
        ):
            raise ClientError(
                "Cursor was not delivered; read inbox before acknowledging its exact cursor"
            )
        # Acknowledgement is idempotent, including a restart after this fsync.
        save(path, {"scope": scope, "cursor": cursor})
        emit({"cursor": cursor, "acknowledged": True}, client.token)
        return
    result = client.inbox(after, limit=args.limit)
    cursor = result.get("cursor")
    if (
        type(cursor) is not int
        or not after <= cursor <= 9_007_199_254_740_991
        or not isinstance(result.get("events"), list)
    ):
        raise ClientError("Invalid inbox response; saved cursor left unchanged")
    # Reading (including --peek) never acknowledges application processing.
    # Only a complete flushed page is offered for later explicit --ack. A crash
    # before recording it safely replays the page instead of losing events.
    emit(result, client.token)
    save(offered_path, {"scope": scope, "after": after, "cursor": cursor})


def emit(body: JsonObject, token: str) -> None:
    # Even an untrusted room echoing the credential cannot print it to the shell.
    encoded = json.dumps(
        {"http": 200, "body": body}, ensure_ascii=True, allow_nan=False
    )
    encoded = encoded.replace(token, "[redacted]")
    write_line(encoded, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="room_cli")
    parser.add_argument("url")
    parser.add_argument("token_file")
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="private state root (default: .room-cli beside the token file)",
    )
    parser.add_argument("--ca-file")
    parser.add_argument("--timeout", type=float, default=10)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("identity")
    commands.add_parser("definition")
    view = commands.add_parser("view")
    view.add_argument("--compact", action="store_true")
    propose = commands.add_parser("propose")
    propose.add_argument("kind", nargs="?")
    propose.add_argument("payload", nargs="?")
    propose.add_argument("--retry", action="store_true")
    propose.add_argument("--expected-version", type=int)
    poll = commands.add_parser("inbox")
    poll.add_argument("--limit", type=int, default=16, choices=range(1, 129))
    mode = poll.add_mutually_exclusive_group()
    mode.add_argument(
        "--peek", action="store_true", help="read without acknowledging (the default)"
    )
    mode.add_argument(
        "--ack",
        type=int,
        metavar="CURSOR",
        help="acknowledge the exact cursor of a successfully processed page",
    )
    commands.add_parser("wakes", help="pending outbound receipt wakes; no sends")
    confirm = commands.add_parser(
        "wake-confirm", help="record checked transport acceptance"
    )
    confirm.add_argument("receipt")
    confirm.add_argument("recipient")
    commands.add_parser("wake-inbox", help="durably recover one authority inbox page")
    receive = commands.add_parser(
        "wake-receive", help="recover then durably claim a receipt"
    )
    receive.add_argument("receipt")
    complete = commands.add_parser(
        "wake-complete", help="assert durable completion/reconciliation"
    )
    complete.add_argument("receipt")
    reconcile = commands.add_parser(
        "wake-reconcile", help="host reconciliation of held effects"
    )
    reconcile.add_argument("receipt")
    reconcile.add_argument("decision", choices=("retry", "completed"))
    reconcile.add_argument("evidence")
    args = parser.parse_args(argv)
    token = None
    try:
        require_runtime()
        token = private_read(args.token_file).decode("ascii").strip()
        client = HTTPRoom(
            args.url,
            token,
            ca_file=args.ca_file,
            timeout=args.timeout,
            require_inbox=args.command in ("inbox", "wake-inbox", "wake-receive"),
        )
        if args.command == "identity":
            result = client._request("GET", "/v1/identity")
        elif args.command == "view":
            # Bypass runner hydration: DF-05 compact API needs no definition read.
            result = client._request("POST", "/v1/view", {})
            if args.compact:
                result["view"].pop("definition", None)
        elif args.command == "definition":
            result = definition_for(client)
        else:
            root = (
                args.state_dir or Path(args.token_file).absolute().parent / ".room-cli"
            )
            scope = scope_for(client)
            with state_lock(root, scope) as directory:
                if args.command == "inbox":
                    inbox(args, client, directory, scope)
                    return 0
                if args.command == "propose":
                    result, code = proposal(args, client, directory, scope)
                else:
                    # A crash can fall between the room transaction and local
                    # receipt staging. Reconcile the saved exact envelope first.
                    if args.command == "wakes":
                        staged = checked_record(load(directory / "staged.json"), scope)
                        if staged is not None and not staged["resolved"]:
                            _, recovered_code = proposal(
                                argparse.Namespace(
                                    retry=True,
                                    kind=None,
                                    payload=None,
                                    expected_version=None,
                                ),
                                client,
                                directory,
                                scope,
                            )
                            if recovered_code:
                                raise ClientError(
                                    "Saved proposal was rejected; no wake staged"
                                )
                    with WakeJournal(directory / "wakes.sqlite3", scope) as journal:
                        if args.command == "wakes":
                            result = {"pending": journal.pending()}
                        elif args.command == "wake-confirm":
                            journal.confirm(args.receipt, args.recipient)
                            result = {"status": "confirmed", "receipt": args.receipt}
                        elif args.command == "wake-inbox":
                            result = journal.recover(client)
                        elif args.command == "wake-receive":
                            journal.recover(client)
                            result = journal.claim(args.receipt)
                        elif args.command == "wake-reconcile":
                            journal.reconcile(
                                args.receipt, args.decision, args.evidence
                            )
                            result = {"status": args.decision, "receipt": args.receipt}
                        else:
                            journal.complete(args.receipt)
                            result = {"status": "completed", "receipt": args.receipt}
                        code = 2 if result.get("status") == "held" else 0
            emit(result, token)
            return code
        emit(result, token)
        return 0
    except UnsupportedHostError as error:
        write_line(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except (ClientError, TransportError) as error:
        message = str(error)
        if token:
            message = message.replace(token, "[redacted]")
        body: JsonObject = {"error": message}
        if isinstance(error, PreflightError):
            body.update(error.detail)
        encoded = json.dumps(body, ensure_ascii=True)
        if token:
            encoded = encoded.replace(token, "[redacted]")
        write_line(encoded, file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, StoreError):
        write_line(
            json.dumps({"error": "Invalid input, private state or room response"}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
