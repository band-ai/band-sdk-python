"""Reference room authority and separate local checker, in separate OS processes."""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from remote_agent.capabilities import require_linux
from remote_agent.environment import minimal_environment
from remote_agent.storage import StoreError, canonical, digest
from remote_agent.types import JsonObject, SourcePath


class TransportError(Exception):
    pass


class EngineCompileError(TransportError):
    """A safe compiler diagnostic, never raw engine stderr or configuration."""

    def __init__(self, code: str, line: int) -> None:
        self.diagnostic = {"code": code, "line": line}
        super().__init__(f"Contract compilation failed: {code} at line {line}")


class InboxCursorConflict(TransportError):
    """An inbox 409 requires durable host reconciliation, not transport retry."""

    status = 409


class InputVerificationError(TransportError):
    """A definitive input failure, not an unavailable authority."""

    def __init__(self, message: str, status: int = 409) -> None:
        self.status = status
        super().__init__(message)


def reference_room_policy(bindings: dict[str, str]) -> JsonObject:
    """Host-built room_policy (D-15) from a reference room's own membership record.

    A reference room's membership is its configured bindings: every bound actor is an
    active agent with participant permission. The kind comes from this record, never
    from an actor ID or a payload. Callers build it per call and never cache it.
    """
    if not isinstance(bindings, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in bindings.items()
    ):
        raise TransportError("Room membership requires string role bindings")
    return {
        "revision": "bindings-sha256:" + digest(bindings),
        "fixed_roles": {},
        "principals": {
            actor: {"kind": "agent", "active": True, "permission": "participant"}
            for actor in bindings.values()
        },
    }


class EngineProcess:
    def __init__(
        self,
        contract: SourcePath,
        *,
        command: list[str] | None = None,
        format: str = "frontmatter",
        timeout: float = 10,
        max_bytes: int = 2_097_152,
    ) -> None:
        require_linux()
        if format not in ("frontmatter", "standard", "file-source") or (
            command is not None
            and (
                not isinstance(command, list)
                or not command
                or any(not isinstance(x, str) or not x for x in command)
            )
        ):
            raise TransportError(
                "Engine requires a fixed argv and a supported source format"
            )
        self.timeout, self.lock, self.buffer = timeout, threading.RLock(), b""
        self.max_bytes = max_bytes
        if command is None:
            lab = Path(__file__).resolve().parents[2] / "contracts" / "contract-lab"
            paths = sorted((lab / "_build/dev/lib").glob("*/ebin"))
            if not paths:
                raise TransportError(
                    "Configure an installed contract-engine command, or run mix compile for development"
                )
            argv = ["elixir", "--erl", "+S 2:2"]
            for path in paths:
                argv.extend(["-pa", str(path)])
            argv.append(str(lab / "examples/engine_endpoint.exs"))
        else:
            argv = list(command)
        argv.extend([str(Path(contract).resolve()), format])
        # A file avoids a full stderr pipe blocking the engine. Only bounded code/line
        # extraction during startup is public; raw diagnostics are never logged.
        with ExitStack() as resources:
            self.stderr = resources.enter_context(tempfile.TemporaryFile())
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                env=minimal_environment(),
            )
            self.resources = resources.pop_all()
        self.closed = False
        try:
            stdin, stdout = self.process.stdin, self.process.stdout
            assert stdin is not None and stdout is not None
            self.stdin = self.resources.enter_context(stdin)
            self.stdout = self.resources.enter_context(stdout)
            os.set_blocking(self.stdin.fileno(), False)
            self.identity = self._read(startup=True)
            if not self.identity.get("ready"):
                raise TransportError("Engine did not become ready")
        except Exception:
            self.close()
            raise

    def _read(
        self, deadline: float | None = None, *, startup: bool = False
    ) -> JsonObject:
        deadline = deadline or time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TransportError("Engine response timeout")
                chunk = os.read(self.stdout.fileno(), 65536)
                if not chunk:
                    if startup:
                        self.stderr.seek(0)
                        diagnostic = self.stderr.read(65_536).decode(
                            "utf-8", errors="replace"
                        )
                        code = re.search(
                            r"\bcode:\s*:([a-z][a-z0-9_]{0,63})\b", diagnostic
                        )
                        line = re.search(r"\bline:\s*(\d{1,9})\b", diagnostic)
                        if code and line:
                            raise EngineCompileError(code.group(1), int(line.group(1)))
                    raise TransportError("Engine exited")
                self.buffer += chunk
                if len(self.buffer) > self.max_bytes:
                    raise TransportError("Engine response capacity")
        line, self.buffer = self.buffer.split(b"\n", 1)
        result = json.loads(line)
        if "error" in result:
            raise TransportError(str(result["error"]))
        return result

    def _write(self, encoded: bytes, deadline: float) -> None:
        remaining_bytes = memoryview(encoded)
        with selectors.DefaultSelector() as selector:
            selector.register(self.stdin, selectors.EVENT_WRITE)
            while remaining_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TransportError("Engine request timeout")
                try:
                    written = os.write(self.stdin.fileno(), remaining_bytes)
                except BlockingIOError:
                    continue
                remaining_bytes = remaining_bytes[written:]

    def request(self, **request: Any) -> JsonObject:
        with self.lock:
            if self.closed:
                raise TransportError("Engine transport is closed")
            encoded = canonical(request) + b"\n"
            if len(encoded) > self.max_bytes:
                raise TransportError("Engine request capacity")
            deadline = time.monotonic() + self.timeout
            try:
                self._write(encoded, deadline)
                return self._read(deadline)
            except Exception as error:
                # A timeout invalidates the stream; never pair a late reply with a new request.
                self.close(force=True)
                if isinstance(error, TransportError):
                    raise
                raise TransportError("Engine transport failed") from error

    def close(self, *, force: bool = False) -> None:
        with self.lock:
            if not self.closed:
                self.closed = True
                if force:
                    self.process.kill()
                else:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                self.resources.close()


class LocalGate:
    def __init__(self, engine: EngineProcess) -> None:
        self.engine = engine

    def check(
        self, projection: JsonObject, message: JsonObject, actor: str, now: int
    ) -> JsonObject:
        # A remote host's clock epoch is not the room's clock authority.
        return self.engine.request(
            op="check",
            projection=projection,
            message=message,
            actor=actor,
            now=projection.get("now", now),
        )


class ReferenceRoom:
    """Trusted host pipe. Production transport must authenticate room and participant."""

    idempotent = True

    # The host capabilities stated on new when the caller states none: this room hosts the
    # reference engine, which needs nothing more from the host for reply waits.
    CAPABILITIES = ("contract.replies/1",)

    def __init__(
        self,
        engine: EngineProcess,
        *,
        session: str,
        bindings: dict[str, str],
        now: int = 0,
        capabilities: Sequence[str] | None = None,
    ) -> None:
        policy = reference_room_policy(bindings)
        # The room's own membership record; a caller's later mutation cannot change it.
        self.engine, self.session, self.bindings = engine, session, dict(bindings)
        self.capabilities = list(
            self.CAPABILITIES if capabilities is None else capabilities
        )
        engine.request(
            op="new",
            session=session,
            bindings=self.bindings,
            now=now,
            inactivity="accepted",
            max_messages=100,
            room_policy=policy,
            capabilities=self.capabilities,
        )

    @property
    def identity(self) -> JsonObject:
        return self.engine.identity

    def view(self, actor: str, now: int) -> JsonObject:
        return self.engine.request(
            op="view",
            actor=actor,
            now=now,
            room_policy=reference_room_policy(self.bindings),
        )

    def publish(self, message: JsonObject, actor: str, now: int) -> JsonObject:
        return self.engine.request(
            op="step",
            message=message,
            actor=actor,
            now=now,
            room_policy=reference_room_policy(self.bindings),
        )

    def lookup(self, message: JsonObject, actor: str, now: int) -> JsonObject:
        # The reference engine deduplicates the exact envelope before current-state admission.
        # A safe retry itself obtains the durable destination decision. Other adapters can query.
        return self.publish(message, actor, now)

    def accepted_input(self, event: JsonObject, actor: str, now: int) -> JsonObject:
        if (
            not isinstance(event, dict)
            or "payload" not in event
            or any(
                not isinstance(event.get(key), str) or not event[key]
                for key in ("id", "session", "kind", "actor", "role")
            )
        ):
            raise InputVerificationError("Malformed accepted-event body", 400)
        try:
            canonical(event)
        except StoreError:
            raise InputVerificationError("Malformed accepted-event body", 400) from None
        view = self.view(actor, now)["view"]
        fact = view["facts"].get(event.get("id"))
        if (
            not fact
            or (actor != fact["actor"] and actor not in fact["recipients"])
            or event.get("session") != self.session
            or any(event.get(key) != fact[key] for key in ("kind", "actor", "role"))
            or canonical(event["payload"]) != canonical(fact["payload"])
        ):
            raise InputVerificationError(
                "Input is not a matching room fact authored by or addressed to this participant"
            )
        return {
            "id": fact["id"],
            "session": self.session,
            "kind": fact["kind"],
            "payload": fact["payload"],
            "actor": fact["actor"],
            "role": fact["role"],
        }
