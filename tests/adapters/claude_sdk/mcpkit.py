"""Connection snapshots, hostile callback values and retained startup doubles."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from band.core.exceptions import CleanupIncompleteError
from band.integrations.claude_sdk.mcp_startup import startup_error
from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from band.runtime.tools import BAND_MCP_SERVER_NAME
from band.workspaces import RoomWorkspaces
from tests.adapters.claude_sdk.fakecli import FakeClaude, FakeCLISession
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, lease_client

GRANT_A = "grant-a-5ecret"
GRANT_B = "grant-b-5ecret"


class Resolver:
    """Returns ``servers`` and records each workspace it was asked for."""

    def __init__(self, servers: Mapping[str, Any]) -> None:
        self.servers = servers
        self.calls: list[str] = []

    async def __call__(self, workspace: str) -> Mapping[str, Any]:
        self.calls.append(workspace)
        return self.servers


def launch_file(session: FakeCLISession) -> Path:
    servers = session.options.mcp_servers
    assert isinstance(servers, str), "a resolver's servers go through a file"
    return Path(servers)


def connected(*names: str) -> list[dict[str, Any]]:
    return [{"name": name, "status": "connected"} for name in names]


def assert_secret_free(secret: str, error: BaseException, logs: str) -> None:
    rendered = [str(error), repr(error), logs]
    chained: BaseException | None = error
    while chained is not None:
        rendered.append(str(chained))
        chained = chained.__cause__ or (
            None if chained.__suppress_context__ else chained.__context__
        )
    assert not any(secret in text for text in rendered)


class ExplodingServers(Mapping[str, Any]):
    """A mapping that fails while being read, with a credential in its error."""

    def __getitem__(self, key: str) -> Any:
        raise LookupError(f"vault said {GRANT_A}")

    def __iter__(self) -> Any:
        raise LookupError(f"vault said {GRANT_A}")

    def __len__(self) -> int:
        return 1


def private_files() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("band-claude-mcp-*/mcp.json"))


class MarkedServers(Mapping[str, Any]):
    """A lazy mapping raising the module's own safe-looking error type, with a
    credential in its reason and resume eligibility set."""

    def __getitem__(self, key: str) -> Any:
        raise startup_error("launch", f"token {GRANT_A}", resume_eligible=True)

    def __iter__(self) -> Any:
        raise startup_error("launch", f"token {GRANT_A}", resume_eligible=True)

    def __len__(self) -> int:
        return 1


def item_failure(kind: str) -> BaseException:
    if kind == "ordinary":
        return LookupError(f"vault said {GRANT_A}")
    if kind == "marked":
        return startup_error("launch", f"token {GRANT_A}", resume_eligible=True)
    # A callback-defined exception whose very class name carries the secret.
    return type(f"Leak_{GRANT_A.replace('-', '_')}", (Exception,), {})()


class BadItem(tuple):
    """A ``(name, config)`` item that fails when unpacked."""

    failure: BaseException

    def __iter__(self) -> Any:
        raise self.failure


class HostileItems(Mapping[str, Any]):
    """A mapping whose items fail only once a caller unpacks them."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def __getitem__(self, key: str) -> Any:
        return {"type": "stdio", "command": "tool"}

    def __iter__(self) -> Any:
        return iter(["tool"])

    def __len__(self) -> int:
        return 1

    def items(self) -> Any:
        item = BadItem(("tool", {"type": "stdio", "command": "tool"}))
        item.failure = self.failure
        return [item]


class _ExplodingMeta(type):
    """A metaclass whose class metadata raises a secret-bearing error."""

    def __getattribute__(cls, name: str) -> Any:
        if name in {"__module__", "__name__", "__qualname__"}:
            raise LookupError(f"meta said {GRANT_A}")
        return super().__getattribute__(name)


def metadata_attack(kind: str) -> BaseException:
    secret_name = f"Leak_{GRANT_A.replace('-', '_')}"
    if kind == "spoofed-module":
        cls = type(secret_name, (Exception,), {"__module__": "builtins"})
    elif kind == "non-string-module":
        cls = type(secret_name, (Exception,), {"__module__": 3})
    else:
        cls = _ExplodingMeta(secret_name, (Exception,), {})
    return cls()


BAND = {BAND_MCP_SERVER_NAME: {"type": "http", "url": "http://127.0.0.1:9/band"}}
SECRET = "close-5ecret"


class HeldResolver:
    """Resolves only when released; records calls and cancellations. With
    ``swallow`` it finishes its own work despite a cancellation and returns."""

    def __init__(self, *, swallow: bool = False) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.swallow = swallow
        self.calls: list[str] = []
        self.cancellations = 0
        self.settled = asyncio.Event()

    async def __call__(self, workspace: str) -> Mapping[str, Any]:
        self.calls.append(workspace)
        self.entered.set()
        try:
            while True:
                try:
                    await self.release.wait()
                    break
                except asyncio.CancelledError:
                    self.cancellations += 1
                    if not self.swallow:
                        raise
            return {}
        finally:
            self.settled.set()


def manager_for(
    tmp_path: Path,
    resolver: Any,
    claude: FakeClaude,
    band: dict[str, Any] | None = None,
) -> ClaudeSessionManager:
    """A manager whose protected Band server is ``band`` (changed in place to
    model a replaced Band server)."""
    protected = BAND if band is None else band
    claude.mcp_statuses.append([{"name": BAND_MCP_SERVER_NAME, "status": "connected"}])
    return ClaudeSessionManager(
        ClaudeAgentOptions(),
        mcp_servers_factory=lambda room_id: dict(protected),
        workspaces=RoomWorkspaces(lambda room_id: str(tmp_path / room_id)),
        resolve_mcp_servers=resolver,
        retention=REPORTING_BUDGET,
        settle_grace_s=0,
    )


class SettlingResolver:
    """Already being cancelled (by itself or its own room cleanup) when it
    enters a settlement it must finish; logs any further cancellation."""

    def __init__(self, *, retire_own_room: bool = False) -> None:
        self.retire_own_room = retire_own_room
        self.manager: ClaudeSessionManager | None = None
        self.settling = asyncio.Event()
        self.release = asyncio.Event()
        self.log: list[str] = []

    async def __call__(self, workspace: str) -> Mapping[str, Any]:
        if self.retire_own_room:
            assert self.manager is not None
            with pytest.raises(CleanupIncompleteError):
                await self.manager.cleanup_session("room-a")
        else:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass  # caught, never uncancelled
        self.settling.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.log.append("settlement interrupted")
            raise
        self.log.append("settled")
        return {}


class CountingResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, workspace: str) -> Mapping[str, Any]:
        self.calls.append(workspace)
        return {}


async def outdated_session(
    tmp_path: Path, claude: FakeClaude
) -> tuple[ClaudeSessionManager, CountingResolver, dict[str, Any]]:
    """A manager whose room-a session (A) dials a Band server since replaced."""
    resolver = CountingResolver()
    band = dict(BAND)
    manager = manager_for(tmp_path, resolver, claude, band)
    await lease_client(manager, "room-a")
    band[BAND_MCP_SERVER_NAME] = {"type": "http", "url": "http://127.0.0.1:9/band-2"}
    return manager, resolver, band


class FailingBand:
    """A protected-server factory that can be made to fail, with a secret."""

    def __init__(self) -> None:
        self.failing = False

    def __call__(self, room_id: str) -> dict[str, Any]:
        if self.failing:
            raise LookupError(f"band backend said {SECRET}")
        return dict(BAND)


class ReuseProbe:
    """The servers a held client dials, as a callback would hand them over: the
    comparison answers with this callback's own object, whose truth is therefore
    consulted wherever the decision is read (an ``and`` chain coerces each
    earlier operand and leaves the final one, and the settled result is read
    again at the branch). Closing admission from the second read models a
    callback acting when the decision is read after the final admission guard;
    closing on the first read is the early control. The client itself stays the
    manager's real one, so its teardown stays owned."""

    def __init__(self, manager: ClaudeSessionManager, mode: str) -> None:
        self.manager = manager
        self.mode = mode
        self.reads = 0

    def _decide(self) -> bool:
        self.reads += 1
        closing_now = (
            self.reads == 1 if self.mode == "materialized" else self.reads >= 2
        )
        if closing_now:
            self.manager.close_admission()
        return True

    def __eq__(self, other: object) -> Any:
        return self

    def __bool__(self) -> bool:
        return self._decide()

    __hash__ = None  # type: ignore[assignment]


class HeldServers:
    """A stand-in for the held client's options: only its servers are read."""

    def __init__(self, servers: Any) -> None:
        self.mcp_servers = servers


class ClientCustody:
    """What teardown must leave behind on an owned client: one disconnect, its
    Query closed on both sides, and every reference dropped. A manager that
    reports nothing held while the client it owned stays connected is the
    failure this pins, so the checks read the client, not the manager."""

    def __init__(self, client: ClaudeSDKClient) -> None:
        self.client = client
        self.query = client._query
        self.disconnects = 0
        self.calls: list[str] = []
        assert self.query is not None, "the client was never connected"
        query, real_disconnect = self.query, client.disconnect
        real_close, real_close_receive = query.close, query.close_receive_stream

        async def disconnect() -> None:
            self.disconnects += 1
            await real_disconnect()

        async def close() -> None:
            self.calls.append("close")
            await real_close()

        def close_receive() -> None:
            self.calls.append("receive")
            real_close_receive()

        client.disconnect = disconnect  # type: ignore[method-assign]
        query.close = close  # type: ignore[method-assign]
        query.close_receive_stream = close_receive  # type: ignore[method-assign]

    def assert_disconnected(self) -> None:
        assert self.disconnects == 1, f"disconnect ran {self.disconnects} times"
        assert self.calls == ["close", "receive"], self.calls
        assert self.query._closed, "the client's Query stayed open"
        assert self.client._query is None
        assert self.client._transport is None
        assert self.client._materialized is None


class RaisingTruth:
    """A callback's servers whose comparison answers with the callback's own
    object and whose truth test raises a secret-bearing error of a builtin
    type, as a callback-computed step could."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.reads = 0

    def __eq__(self, other: object) -> Any:
        return self

    def __bool__(self) -> bool:
        self.reads += 1
        raise LookupError(self.text)

    __hash__ = None  # type: ignore[assignment]
