"""Native OMP runtime doubles and subprocess turn helpers."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.integrations.ompprocess import OmpMcpServer, OmpMcpTransport
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import FakeAgentTools, reported_failures
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omprpc.kit import process_is_alive
from tests.paths import REPO_ROOT


class PendingRuntime:
    def __init__(self, *_args, **_kwargs):
        self.yielded = asyncio.Event()
        self.finish = asyncio.Event()
        self.closed = False
        self.reply = True

    async def start(self):
        return None

    async def request(self, command, **_parameters):
        if command == "get_state":
            return {
                "sessionId": "native-session",
                "sessionFile": None,
                "isSettled": True,
            }
        return {}

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        self.yielded.set()
        await self.finish.wait()
        if self.reply:
            await on_tool(
                OmpHostToolCall(
                    id="host-call",
                    toolCallId="reply",
                    toolName="band_send_message",
                    arguments={"content": "Background work finished"},
                )
            )

    async def close(self):
        self.closed = True


class FailedDeliveryTools(FakeAgentTools):
    async def execute_tool_call_structured(self, tool_name, arguments):
        return ToolCallOutcome(
            value={"error": "delivery rejected"},
            ok=False,
            error_message="delivery rejected",
        )


class AutomaticThinkingRuntime(PendingRuntime):
    async def request(self, command, **parameters):
        if command == "get_available_thinking_levels":
            return {"levels": ["off", "low", "high"]}
        if command == "get_state":
            return {
                "sessionId": "native-session",
                "isSettled": True,
                "thinkingLevel": "high",
            }
        return await super().request(command, **parameters)


class DocumentationRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_event(
            OmpEvent(
                type="tool_execution_start",
                toolCallId="docs",
                toolName="grep",
                args={
                    "path": "xd://mcp__band_sdk_band_band_send_message",
                    "pattern": ".",
                },
            )
        )
        await on_event(
            OmpEvent(
                type="tool_execution_end",
                toolCallId="docs",
                toolName="grep",
                result=OmpToolResult(
                    content=[OmpContent(type="text", text="Tool documentation")]
                ),
                isError=False,
            )
        )


class RepeatedRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        request = OmpUIRequest(id="ui-wire-id", method="select", title="A question")
        for _ in range(2):
            response = await on_ui(request)
            assert response.cancelled
        await on_event(
            OmpEvent(
                type="tool_execution_start",
                toolCallId="ui-wire-id",
                toolName="read",
                args={"path": "denied.txt"},
            )
        )
        await on_event(
            OmpEvent(
                type="tool_execution_end",
                toolCallId="ui-wire-id",
                toolName="read",
                isError=True,
                result=OmpToolResult(
                    content=[OmpContent(type="text", text="Native tool failed")]
                ),
            )
        )


async def run_turn(adapter, tools, *, bootstrap=True):
    await adapter.run_judged_turn(
        msg=make_platform_message("Finish the background work"),
        tools=tools,
        history=OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id="room-123",
    )


async def _ready(*_args, **_kwargs) -> None:
    return None


async def _cancel(_request) -> None:
    return None


async def owned_rpc_turn(
    monkeypatch,
    tmp_path: Path,
    title: str,
    order: str,
    *,
    native_name: str = "read",
    native_arguments=None,
    native_error: bool = True,
    inject_band_tools: bool = True,
):
    observed = []
    clients = []

    class OrderingClient(OmpRpcClient):
        async def start(self):
            await super().start()
            assert self._proc is not None
            root_identity = await asyncio.to_thread(psutil.Process, self._proc.pid)
            checkpoint = await self.request("checkpoint")
            peer_pid = checkpoint["pid"]
            assert isinstance(peer_pid, int)
            peer_identity = await asyncio.to_thread(psutil.Process, peer_pid)
            await asyncio.to_thread(peer_identity.create_time)
            assert peer_identity == root_identity or root_identity in (
                await asyncio.to_thread(peer_identity.parents)
            )
            clients.append((self, peer_identity))

        async def prompt(self, message, *, on_event, on_tool, on_ui):
            async def event(frame):
                if frame.type in {"tool_execution_start", "tool_execution_end"}:
                    observed.append(frame.type)
                await on_event(frame)
                if frame.type == "tool_execution_start" and order == "native-first":
                    await self.request("permission-after-native")

            async def ui(request):
                observed.append("select")
                return await on_ui(request)

            await super().prompt(message, on_event=event, on_tool=on_tool, on_ui=ui)

    def client_factory(*_args, env=None, **_kwargs):
        client = OrderingClient(
            (
                sys.executable,
                "-u",
                str(REPO_ROOT / "tests" / "integrations" / "omppermissionpeer.py"),
                order,
            ),
            env={
                **(env or {}),
                "PYTHONPATH": str(REPO_ROOT),
                "OMP_TEST_PERMISSION_TITLE": title,
                "OMP_TEST_NATIVE_NAME": native_name,
                "OMP_TEST_NATIVE_ARGUMENTS": json.dumps(
                    {"path": "denied.txt"}
                    if native_arguments is None
                    else native_arguments
                ),
                "OMP_TEST_NATIVE_ERROR": "1" if native_error else "0",
            },
        )
        return client

    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", client_factory)
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", _ready)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=inject_band_tools),
        resolve_permission=_cancel,
    )
    tools = FakeAgentTools()
    try:
        await adapter.on_message(
            make_platform_message("Handle the permission request"),
            tools,
            OmpSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room",
        )
    finally:
        await adapter.cleanup_all()
    assert clients
    for client, peer_identity in clients:
        process = client._proc
        assert process is not None
        assert process.returncode is not None
        assert await process.wait() == process.returncode
        closing = client._close_task
        assert closing is not None and closing.done() and not closing.cancelled()
        assert closing.exception() is None
        # The retained helper can differ from the native interpreter on Windows.
        assert not await asyncio.to_thread(process_is_alive, peer_identity)
    assert adapter._rooms == {}
    return tools, observed


class Launches:
    """Record native runtime instances and the startup overlays they own."""

    def __init__(self) -> None:
        self.runtimes: list[PendingRuntime] = []
        self.servers: list[dict[str, object]] = []
        self.plugins: list[Path] = []
        self.spawned = asyncio.Event()

    def __call__(self, command: list[str], **_kwargs: object) -> PendingRuntime:
        plugin = command[command.index("--plugin-dir") + 1]
        overlay = json.loads((Path(plugin) / ".mcp.json").read_text(encoding="utf-8"))
        self.servers.append(overlay["mcpServers"])
        self.plugins.append(Path(plugin))
        runtime = PendingRuntime()
        runtime.finish.set()
        self.runtimes.append(runtime)
        self.spawned.set()
        return runtime


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> Launches:
    record = Launches()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", record)
    return record


def remote(token: str) -> OmpMcpServer:
    return OmpMcpServer(
        type=OmpMcpTransport.HTTP,
        url="http://127.0.0.1:9/mcp",
        headers={"Authorization": f"Bearer {token}"},
    )


class BandMcpReplyRuntime(PendingRuntime):
    """Replies through the Band MCP server mounted in the overlay, not a host tool."""

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        name = "mcp__band_sdk_band_send_message"
        args = {"content": "done"}
        await on_event(
            OmpEvent(
                type="tool_execution_start", toolCallId="c1", toolName=name, args=args
            )
        )
        await on_event(
            OmpEvent(
                type="tool_execution_end", toolCallId="c1", toolName=name, isError=False
            )
        )


class SignallingLock(asyncio.Lock):
    """A room lock that reports when a second turn starts waiting for it."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> bool:
        if self.locked():
            self.contended.set()
        return await super().acquire()


class SettlingResolver:
    """Blocks the first resolution; when cancelled it settles durable work before
    re-raising, and counts every cancellation it receives along the way."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.settle = asyncio.Event()
        self.settled = False
        self.cancellations = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1  # settlement interrupted by a second cancel
                raise
            self.settled = True
            raise
        return {}


def settling_adapter(tmp_path: Path, resolver, **config):
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), **config),
        resolve_mcp_servers=resolver,
        emit=(),
    )
    releases: list[bool] = []  # whether settlement had finished at each release
    real_release = adapter._workspaces.release

    def release(room_id, workspace=None):
        releases.append(resolver.settled)
        return real_release(room_id, workspace)

    adapter._workspaces.release = release  # type: ignore[method-assign]
    return adapter, releases


async def start_blocked_turn(adapter, resolver: SettlingResolver):
    first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
    await asyncio.wait_for(resolver.entered.wait(), 1)
    return first


async def checkpoint() -> None:
    resumed = asyncio.Event()
    asyncio.get_running_loop().call_soon(resumed.set)
    await resumed.wait()


async def start_cleanup(adapter: OmpAdapter, *, shutdown: bool = False):
    entered = asyncio.Event()
    original = adapter.on_cleanup

    async def cleanup(room_id):
        entered.set()
        await original(room_id)

    adapter.on_cleanup = cleanup  # type: ignore[method-assign]
    try:
        operation = (
            adapter.cleanup_all() if shutdown else adapter.on_cleanup("room-123")
        )
        task = asyncio.create_task(operation)
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        adapter.on_cleanup = original  # type: ignore[method-assign]
    return task


async def cross_deadline(clock, seconds: float) -> None:
    crossed = asyncio.Event()
    handle = asyncio.get_running_loop().call_later(seconds, crossed.set)
    try:
        clock.advance(seconds)
        await asyncio.wait_for(crossed.wait(), 1)
    finally:
        handle.cancel()


class SwallowingResolver:
    """Catches a cancellation at its first await and returns a server anyway."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        try:
            await checkpoint()
        except asyncio.CancelledError:
            pass
        return {"linear": remote("late")}


class SelfRetiringResolver(SettlingResolver):
    """On its first cancellation it asks for its own room's cleanup, then holds
    its settlement until released."""

    adapter: OmpAdapter

    async def __call__(self, workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            await self.adapter.on_cleanup("room-123")  # returns at once for self
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise
            self.settled = True
            raise
        return {}


class UncancellingResolver(SettlingResolver):
    """Consumes the deadline's cancellation with the public ``uncancel()``, then
    settles; ``returns`` decides whether it re-raises or returns a server."""

    def __init__(self, *, returns: bool) -> None:
        super().__init__()
        self.returns = returns

    async def __call__(self, _workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()  # the request count is now consumed
            self.cancellations += 1
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise
            self.settled = True
            if not self.returns:
                raise
        return {"linear": remote("late")}


def timeout_reports(tools) -> list[tuple[str, str | None]]:
    return [(f["provider"], f.get("code")) for f in reported_failures(tools)]


def failing_runtime(closing: asyncio.Event, finish_close: asyncio.Event):
    class FailingRuntime(PendingRuntime):
        async def prompt(self, *_args, **_kwargs):
            raise RuntimeError("native turn failed")

        async def close(self):
            closing.set()
            await finish_close.wait()
            self.closed = True

    return FailingRuntime()
