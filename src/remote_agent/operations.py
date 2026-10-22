"""Verified live backups and nonexecuting journal inspection/export/replay."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO

from remote_agent.capabilities import require_linux
from remote_agent.commands import CommandError, result_from_capture
from remote_agent.diagnostics import agent_summary
from remote_agent.hostkey import HostKey
from remote_agent.program import Program, ProgramError
from remote_agent.protocol import decode_journal
from remote_agent.publication import publish_file
from remote_agent.retention import validate_anchor
from remote_agent.storage import StoreError, canonical, digest
from remote_agent.store import Store
from remote_agent.types import JsonObject, SourcePath


@contextmanager
def private_reader(
    path: SourcePath, *, maximum: int = 2_147_483_648
) -> Generator[BinaryIO, None, None]:
    require_linux()
    descriptor = None
    try:
        path = Path(path).absolute()
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise StoreError("Operator input directory must be private to its owner")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > maximum
        ):
            raise StoreError("Operator input must be a bounded private regular file")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            yield source
    except (
        OSError,
        sqlite3.DatabaseError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
    ) as error:
        raise StoreError("Operator input could not be verified") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def readonly_store(path: SourcePath) -> Generator[Store, None, None]:
    """A committed SQLite read snapshot; never owns or mutates the controller."""
    path = Path(path).absolute()
    with private_reader(path, maximum=1_073_741_824):
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if (
                connection.execute("PRAGMA user_version").fetchone()[0] != 1
                or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            ):
                raise StoreError("Unsupported or corrupt database")
            verifier = Store.__new__(Store)
            verifier.db, verifier.path, verifier.lock = (
                connection,
                path,
                threading.RLock(),
            )
            verifier.durable = True
            verifier._verify()
            verifier._verify_snapshots()
            limits = (verifier.get("service_config") or {}).get("storage_limits", {})
            verifier.max_records, verifier.max_database_bytes = (
                limits.get("max_records"),
                limits.get("max_database_bytes"),
            )
            yield verifier
        finally:
            connection.close()


def inspect(path: SourcePath) -> JsonObject:
    with readonly_store(path) as store:
        head = store.connection.execute(
            "SELECT sequence,hash FROM journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        result = {
            "profile": "remote-agent/inspection/1",
            "schema": 1,
            "journal": {
                "records": head[0] if head else 0,
                "head": head[1] if head else "0" * 64,
                "snapshots": store.connection.execute(
                    "SELECT COUNT(*) FROM snapshots"
                ).fetchone()[0],
            },
            "storage": store.metrics(),
        }
        if store.get("room_config"):
            summary = store.get("room_summary")
            result.update(
                kind="room",
                room={k: summary[k] for k in ("version", "status", "state", "outcome")},
            )
        else:
            result.update(
                kind="agent" if store.get("config") else "journal",
                agent=agent_summary(store),
            )
        return result


def journal(source: SourcePath, **page: Any) -> JsonObject:
    """Inspect a bounded journal page without taking controller ownership."""
    with readonly_store(source) as store:
        return store.search(**page)


def backup(source: SourcePath, destination: SourcePath) -> JsonObject:
    host = HostKey(SimpleNamespace(path=Path(source).absolute()))
    with host.locked():
        key = host.read()
        with readonly_store(source) as store:
            return Store._copy_verified(store.connection, destination, host_key=key)


def restore(source: SourcePath, destination: SourcePath) -> JsonObject:
    with private_reader(source):
        return Store.restore(source, destination)


def export(source: SourcePath, destination: SourcePath) -> JsonObject:
    with readonly_store(source) as store:
        row = store.connection.execute(
            "SELECT sequence,hash FROM journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        header = {
            "profile": "remote-agent/journal-export/1",
            "source_schema": 1,
            "records": row[0] if row else 0,
            "head": row[1] if row else "0" * 64,
        }
        anchor = store.anchor()
        if anchor:
            header.update(
                profile="remote-agent/journal-export/2",
                anchor=anchor,
                anchor_sha256=digest(anchor),
            )

        def write(stream: BinaryIO) -> None:
            stream.write(canonical(header) + b"\n")
            for row in store.connection.execute(
                "SELECT * FROM journal ORDER BY sequence"
            ):
                stream.write(canonical(Store._decode(row)) + b"\n")
                if stream.tell() > 2_147_483_648:
                    raise StoreError("Journal export capacity exceeded")

        publish_file(destination, write)
        return header


def recorded(
    archive: SourcePath, identifier: str, *, kind: str = "program"
) -> JsonObject:
    if kind not in ("program", "model"):
        raise StoreError("Unsupported invocation kind")
    with private_reader(archive) as source:
        encoded = source.readline(16_777_217)
        if len(encoded) > 16_777_216 or not encoded.endswith(b"\n"):
            raise StoreError("Invalid journal export header framing")
        header = decode_journal(encoded)
        anchored = (
            isinstance(header, dict)
            and header.get("profile") == "remote-agent/journal-export/2"
        )
        fields = {"profile", "source_schema", "records", "head"} | (
            {"anchor", "anchor_sha256"} if anchored else set()
        )
        if (
            not isinstance(header, dict)
            or set(header) != fields
            or header["profile"]
            not in ("remote-agent/journal-export/1", "remote-agent/journal-export/2")
            or type(header["source_schema"]) is not int
            or header["source_schema"] != 1
            or type(header["records"]) is not int
            or not 0 <= header["records"] <= 10_000_000
            or not isinstance(header["head"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", header["head"])
        ):
            raise StoreError("Invalid journal export header")
        anchor = None
        if anchored:
            anchor = validate_anchor(header["anchor"], header["anchor_sha256"])
        previous, expected = (
            (anchor["head"], anchor["sequence"] + 1) if anchor else ("0" * 64, 1)
        )
        input, execution, index = None, None, None
        total = len(encoded)
        key = ("invocation:" if kind == "program" else "model:") + identifier
        while encoded := source.readline(16_777_217):
            total += len(encoded)
            if (
                len(encoded) > 16_777_216
                or total > 2_147_483_648
                or not encoded.endswith(b"\n")
                or expected > header["records"]
            ):
                raise StoreError("Invalid journal export framing")
            frame = decode_journal(encoded)
            if (
                not isinstance(frame, dict)
                or set(frame)
                != {"sequence", "kind", "data", "updates", "previous", "hash"}
                or type(frame["sequence"]) is not int
                or not isinstance(frame["kind"], str)
                or not 1 <= len(frame["kind"]) <= 100
                or not isinstance(frame["updates"], dict)
            ):
                raise StoreError("Invalid journal export record")
            checksum = frame.pop("hash")
            if (
                frame["sequence"] != expected
                or frame["previous"] != previous
                or digest(frame) != checksum
            ):
                raise StoreError("Corrupt exported journal")
            previous, expected = checksum, expected + 1
            if key in frame["updates"]:
                index = frame["updates"][key]
            if frame["kind"] == kind + "_input" and frame["data"]["id"] == identifier:
                if input is not None:
                    raise StoreError("Ambiguous invocation input")
                input = frame["data"]
            if frame["kind"] == kind + "_output" and frame["data"]["id"] == identifier:
                if (
                    input is None
                    or execution is not None
                    or frame["data"]["input_hash"] != digest(input)
                ):
                    raise StoreError("Invocation output does not match its input")
                execution = frame["data"]["execution"]
        if expected != header["records"] + 1 or previous != header["head"]:
            raise StoreError("Truncated or corrupt exported journal")
        if not input or not index or index["fingerprint"] != digest(input):
            raise StoreError("Unknown or corrupt invocation")
        if (
            index["status"] not in ("started", "completed")
            or (index["status"] == "completed") != (execution is not None)
            or index.get("execution") != execution
        ):
            raise StoreError("Invocation index disagrees with its journal")
        result, failure = None, None
        if execution is not None:
            try:
                if kind == "program":
                    result = Program.result(execution, op=input["request"]["op"])
                elif execution["profile"] == "remote-agent/process-result/1":
                    result = result_from_capture(execution)
                else:
                    result, failure = execution["value"], execution["failure"]
            except (ProgramError, CommandError) as error:
                failure = str(error)
        return {
            "profile": "remote-agent/recorded-result/1",
            "kind": kind,
            "input": input,
            "status": index["status"],
            "execution": execution,
            "result": result,
            "failure": failure,
            "replayable": execution is not None and failure is None,
            "journal_head": previous,
        }


def replay(
    archive: SourcePath,
    identifier: str,
    destination: SourcePath,
    *,
    kind: str = "program",
) -> JsonObject:
    result = recorded(archive, identifier, kind=kind)
    publish_file(destination, lambda stream: stream.write(canonical(result) + b"\n"))
    return {"status": result["status"], "replayable": result["replayable"]}


def rotate_host_key(source: SourcePath) -> JsonObject:
    """A live operator command; SQLite serializes journal commits with the controller."""
    path = Path(source).absolute()
    # Check all input ownership before opening a writable connection. No service
    # owner lock is taken: this is a narrow credential operation, not a controller.
    with private_reader(path):
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=5
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            if (
                connection.execute("PRAGMA user_version").fetchone()[0] != 1
                or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            ):
                raise StoreError("Unsupported or corrupt database")
            store = Store.__new__(Store)
            store.db, store.path, store.lock = connection, path, threading.RLock()
            connection.execute("BEGIN")
            try:
                store._verify()
                store._verify_snapshots()
                limits = (store.get("service_config") or {}).get("storage_limits", {})
            finally:
                connection.execute("ROLLBACK")
            store.max_records = limits.get("max_records", 100_000)
            store.max_record_bytes = limits.get("max_record_bytes", 1_048_576)
            return HostKey(store).rotate()
        finally:
            connection.close()
