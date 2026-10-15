"""Native OMP turn ownership and tool outcome regressions."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import band.integrations.omp as omp_module
from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.omp import OmpMcpServer
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)
from tests.integrations.acp.conftest import make_platform_message


@pytest.fixture(autouse=True)
def native_startup_ready(monkeypatch):
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())


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


@pytest.mark.asyncio
async def test_background_reply_keeps_original_turn_owned(monkeypatch, tmp_path: Path):
    runtime = PendingRuntime()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    tools = FakeAgentTools()
    task = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(runtime.yielded.wait(), 1)
    assert not task.done()
    assert not failure_reports(tools)
    assert not tools.turn.replied
    runtime.finish.set()
    await task
    assert tools.turn.replied
    assert not failure_reports(tools)
    await adapter.cleanup_all()
    assert runtime.closed


@pytest.mark.asyncio
async def test_settled_without_terminal_tool_still_reports_failure(
    monkeypatch, tmp_path: Path
):
    runtime = PendingRuntime()
    runtime.reply = False
    runtime.finish.set()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    tools = FakeAgentTools()
    with pytest.raises(TurnResultAlreadyReported):
        await run_turn(adapter, tools)
    assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
    assert not tools.turn.replied
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_cancelled_background_turn_cannot_own_next_reply(
    monkeypatch, tmp_path: Path
):
    runtimes: asyncio.Queue[PendingRuntime] = asyncio.Queue()

    def spawn(*args, **kwargs):
        runtime = PendingRuntime()
        runtimes.put_nowait(runtime)
        return runtime

    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", spawn)
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    old_tools = FakeAgentTools()
    pending = asyncio.create_task(run_turn(adapter, old_tools))
    old_runtime = await asyncio.wait_for(runtimes.get(), 1)
    await asyncio.wait_for(old_runtime.yielded.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert old_runtime.closed
    new_tools = FakeAgentTools()
    new_tools.fetch_room_context = AsyncMock(return_value={"data": []})
    next_turn = asyncio.create_task(run_turn(adapter, new_tools, bootstrap=False))
    new_runtime = await asyncio.wait_for(runtimes.get(), 1)
    await asyncio.wait_for(new_runtime.yielded.wait(), 1)
    new_runtime.finish.set()
    await next_turn
    assert new_tools.turn.replied
    assert not old_tools.turn.replied
    assert not failure_reports(old_tools)
    await adapter.cleanup_all()


@pytest.mark.parametrize(
    "flag",
    ["--yolo", "--auto-approve", "--approval-mode=yolo", "--approval-mode=write"],
)
def test_launch_flags_cannot_bypass_explicit_approval_setting(flag):
    with pytest.raises(ValueError, match="Unsafe OMP"):
        OmpAdapterConfig(command=("omp", "--mode", "rpc", flag), approval_mode="yolo")


def test_explicit_provider_environment_wins_without_exposing_secrets():
    adapter = OmpAdapter(
        OmpAdapterConfig(
            model="openai/test",
            api_key="derived-secret",
            env={"OPENAI_API_KEY": "explicit-secret"},
        )
    )
    assert adapter._spawn_env()["OPENAI_API_KEY"] == "explicit-secret"
    assert "secret" not in repr(adapter.config)


@pytest.mark.asyncio
async def test_permission_defaults_only_allow_native_approval_choices():
    adapter = OmpAdapter()
    approved = await adapter._permission(
        OmpUIRequest(
            id="approval",
            method="select",
            title="Approve tool?",
            options=["Approve", "Deny"],
        ),
        "room",
        "session",
        FakeAgentTools(),
    )
    unrelated = await adapter._permission(
        OmpUIRequest(
            id="question",
            method="select",
            title="Choose an account",
            options=["personal", "work"],
        ),
        "room",
        "session",
        FakeAgentTools(),
    )
    assert approved.value == "Approve"
    assert unrelated.cancelled


@pytest.mark.asyncio
async def test_permission_resolver_cannot_invent_an_unoffered_choice():
    adapter = OmpAdapter(resolve_permission=AsyncMock(return_value="Allow always"))
    response = await adapter._permission(
        OmpUIRequest(
            id="approval",
            method="select",
            title="Approve tool?",
            options=["Approve", "Deny"],
        ),
        "room",
        "session",
        FakeAgentTools(),
    )
    assert response.cancelled
    assert response.value is None


@pytest.mark.asyncio
async def test_excluded_host_tool_cannot_complete_turn():
    adapter = OmpAdapter(exclude_tools=["band_send_message"])
    tools = FakeAgentTools()
    result = await adapter._execute_tool(
        OmpHostToolCall(
            id="call",
            toolCallId="reply",
            toolName="band_send_message",
            arguments={"content": "Not authorized"},
        ),
        tools,
    )
    assert result.is_error
    assert not tools.turn.replied
    assert not tools.tool_calls


@pytest.mark.asyncio
async def test_failed_band_reply_remains_an_error_for_the_model():
    class FailedDeliveryTools(FakeAgentTools):
        async def execute_tool_call_structured(self, tool_name, arguments):
            return ToolCallOutcome(
                value={"error": "delivery rejected"},
                ok=False,
                error_message="delivery rejected",
            )

    tools = FailedDeliveryTools()
    result = await OmpAdapter()._execute_tool(
        OmpHostToolCall(
            id="call",
            toolCallId="reply",
            toolName="band_send_message",
            arguments={"content": "Answer"},
        ),
        tools,
    )
    assert result.is_error
    assert result.content[0].text == "delivery rejected"
    assert not tools.turn.replied


@pytest.mark.asyncio
@pytest.mark.parametrize("during_setup", [False, True])
async def test_timeout_retires_process_before_reporting_failure(
    monkeypatch, tmp_path: Path, during_setup: bool
):
    runtime = PendingRuntime()
    if during_setup:
        runtime.start = AsyncMock(side_effect=asyncio.Event().wait)
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=0.01), emit=()
    )
    tools = FakeAgentTools()
    with pytest.raises(TimeoutError):
        await run_turn(adapter, tools)
    assert runtime.closed
    assert not adapter._rooms
    assert [
        (failure["provider"], failure.get("code"))
        for failure in reported_failures(tools)
    ] == [("omp", "timeout")]
    assert not tools.turn.replied


@pytest.mark.asyncio
async def test_auto_thinking_can_complete_with_a_concrete_effective_level(
    monkeypatch, tmp_path: Path
):
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

    runtime = AutomaticThinkingRuntime()
    runtime.finish.set()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), reasoning_effort="auto"), emit=()
    )
    tools = FakeAgentTools()
    try:
        await run_turn(adapter, tools)
        assert tools.turn.replied
        assert not failure_reports(tools)
    finally:
        await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_searching_external_reply_tool_docs_does_not_reply(
    monkeypatch, tmp_path: Path
):
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

    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", DocumentationRuntime)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=False), emit=()
    )
    tools = FakeAgentTools()
    try:
        with pytest.raises(TurnResultAlreadyReported):
            await run_turn(adapter, tools)
        assert not tools.turn.replied
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
    finally:
        await adapter.cleanup_all()


# --- per-connection external MCP servers --------------------------------------------------------


class Launches:
    """Records each process the adapter builds, with the MCP overlay it was handed."""

    def __init__(self) -> None:
        self.runtimes: list[PendingRuntime] = []
        self.servers: list[dict[str, object]] = []
        self.plugins: list[Path] = []
        self.spawned = asyncio.Event()

    def __call__(self, command, **_kwargs):
        plugin = command[command.index("--plugin-dir") + 1]
        overlay = json.loads((Path(plugin) / ".mcp.json").read_text(encoding="utf-8"))
        self.servers.append(overlay["mcpServers"])
        self.plugins.append(Path(plugin))
        runtime = PendingRuntime()
        runtime.finish.set()
        self.runtimes.append(runtime)
        self.spawned.set()
        return runtime


def remote(token: str) -> OmpMcpServer:
    return OmpMcpServer(
        type="http",
        url="http://127.0.0.1:9/mcp",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def launches(monkeypatch) -> Launches:
    record = Launches()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", record)
    return record


@pytest.mark.asyncio
async def test_each_new_connection_uses_its_own_resolved_snapshot(
    launches: Launches, tmp_path: Path
):
    current = {"linear": remote("grant-A")}
    seen: list[str] = []

    async def resolve(workspace: str):
        seen.append(workspace)
        return dict(current)

    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
    )
    await run_turn(adapter, FakeAgentTools())
    await adapter.on_cleanup("room-123")  # the native connection is retired
    current["linear"] = remote("grant-B")
    await run_turn(adapter, FakeAgentTools())

    assert [s["linear"]["headers"]["Authorization"] for s in launches.servers] == [
        "Bearer grant-A",
        "Bearer grant-B",
    ]
    claimed = os.path.realpath(tmp_path / "room-123")
    assert seen == [claimed, claimed]  # the claimed workspace, each time
    assert "grant" not in repr(adapter.config)
    assert adapter.config.mcp_servers == {}
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_reusing_a_live_connection_does_not_resolve_again(
    launches: Launches, tmp_path: Path
):
    resolve = AsyncMock(return_value={"linear": remote("grant-A")})
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
    )

    await run_turn(adapter, FakeAgentTools())
    await run_turn(adapter, FakeAgentTools())

    assert resolve.await_count == 1
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_resolved_set_replaces_static_servers_even_when_empty(
    launches: Launches, tmp_path: Path, monkeypatch
):
    ready = AsyncMock()
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), mcp_servers={"stale": remote("old")}),
        resolve_mcp_servers=AsyncMock(return_value={}),
        emit=(),
    )

    await run_turn(adapter, FakeAgentTools())

    assert launches.servers == [{}]
    assert ready.await_args.args[2] == {}  # readiness expects the same empty set
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_readiness_checks_the_same_snapshot_the_overlay_carries(
    launches: Launches, tmp_path: Path, monkeypatch
):
    ready = AsyncMock()
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
    snapshots = iter([{"linear": remote("grant-A")}, {"linear": remote("grant-B")}])

    async def resolve(_workspace: str):
        return next(snapshots)

    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
    )
    await run_turn(adapter, FakeAgentTools())

    assert ready.await_count == 1
    assert ready.await_args.args[2] == {"linear": remote("grant-A")}
    assert launches.servers[0]["linear"]["headers"]["Authorization"] == "Bearer grant-A"
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_failed_resolution_launches_nothing_and_a_later_one_recovers(
    launches: Launches, tmp_path: Path
):
    resolve = AsyncMock(
        side_effect=[RuntimeError("grant unavailable"), {"linear": remote("grant-B")}]
    )
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), mcp_servers={"linear": remote("static")}),
        resolve_mcp_servers=resolve,
        emit=(),
    )
    failed = FakeAgentTools()

    with pytest.raises(RuntimeError, match="grant unavailable"):
        await run_turn(adapter, failed)

    assert launches.runtimes == []  # no static fallback, no process
    assert failure_reports(failed)
    assert not adapter._workspaces.rooms  # the claim was released
    await run_turn(adapter, FakeAgentTools())
    assert [s["linear"]["headers"]["Authorization"] for s in launches.servers] == [
        "Bearer grant-B"
    ]
    await adapter.cleanup_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver_settles", [False, True])
async def test_shutdown_during_resolution_leaves_no_overlay_or_process(
    launches: Launches, tmp_path: Path, monkeypatch, resolver_settles: bool
):
    entered = asyncio.Event()
    overlays: list[str] = []
    real_plugin = omp_module.omp_mcp_plugin

    def recording_plugin(servers, **kwargs):
        overlays.append(kwargs["workspace"])
        return real_plugin(servers, **kwargs)

    monkeypatch.setattr("band.adapters.omp.omp_mcp_plugin", recording_plugin)

    async def resolve(_workspace: str):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not resolver_settles:
                raise
            # A resolver may settle durable work and return instead of re-raising.
            await asyncio.sleep(0)
        return {"linear": remote("late")}

    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
    )
    turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
    await asyncio.wait_for(entered.wait(), 1)

    await asyncio.wait_for(adapter.cleanup_all(), 1)

    assert turn.done()
    assert overlays == [] and launches.runtimes == []
    assert not adapter._workspaces.rooms
    with pytest.raises(asyncio.CancelledError):
        await turn


@pytest.mark.asyncio
async def test_a_blocked_resolution_does_not_hold_another_room(
    launches: Launches, tmp_path: Path
):
    release = asyncio.Event()
    slow_entered = asyncio.Event()

    async def resolve(workspace: str):
        if workspace.endswith("slow"):
            slow_entered.set()
            await release.wait()
        return {}

    adapter = OmpAdapter(
        resolve_mcp_servers=resolve,
        workspace_for_room=lambda room: str(tmp_path / room),
        emit=(),
    )
    for room in ("slow", "fast"):
        (tmp_path / room).mkdir()

    async def turn(room: str):
        await adapter.run_judged_turn(
            msg=make_platform_message("hello"),
            tools=FakeAgentTools(),
            history=OmpSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=room,
        )

    slow = asyncio.create_task(turn("slow"))
    await asyncio.wait_for(slow_entered.wait(), 1)  # slow is now inside its resolver
    await asyncio.wait_for(turn("fast"), 1)

    assert not release.is_set() and not slow.done()  # fast finished while slow blocked
    assert len(launches.runtimes) == 1
    release.set()
    await asyncio.wait_for(slow, 1)
    assert len(launches.runtimes) == 2
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_resolved_stdio_paths_are_anchored_in_the_claimed_workspace(
    launches: Launches, tmp_path: Path
):
    server = OmpMcpServer(command="./bin/tool", cwd="tools", env={"TOKEN": "s3cret"})
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)),
        resolve_mcp_servers=AsyncMock(return_value={"tool": server}),
        emit=(),
    )

    await run_turn(adapter, FakeAgentTools())

    (servers,) = launches.servers
    workspace = Path(os.path.realpath(tmp_path)) / "room-123"
    assert servers["tool"]["cwd"] == str(workspace / "tools")
    assert servers["tool"]["command"] == str(workspace / "tools" / "bin" / "tool")
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_resolved_server_cannot_take_the_band_tool_namespace(
    launches: Launches, tmp_path: Path
):
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)),
        resolve_mcp_servers=AsyncMock(return_value={"band": remote("x")}),
        emit=(),
    )
    tools = FakeAgentTools()

    with pytest.raises(ValueError, match="reserved"):
        await run_turn(adapter, tools)

    assert launches.runtimes == []
    assert failure_reports(tools)


@pytest.mark.asyncio
async def test_a_restored_session_survives_a_resolved_connection(
    launches: Launches, tmp_path: Path
):
    session_dir = Path(os.path.realpath(tmp_path)) / "room-123" / ".band-omp-sessions"
    session_dir.mkdir(parents=True)
    saved = session_dir / "saved.jsonl"
    saved.write_text("{}", encoding="utf-8")
    requests: list[str] = []
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)),
        resolve_mcp_servers=AsyncMock(return_value={"linear": remote("grant-A")}),
        emit=(),
    )

    async def bootstrap_turn():
        await adapter.run_judged_turn(
            msg=make_platform_message("hello"),
            tools=FakeAgentTools(),
            history=OmpSessionState(room_to_session={"room-123": str(saved)}),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

    original = PendingRuntime.request

    async def recording(self, command, **parameters):
        requests.append(command)
        if command == "switch_session":
            return {"cancelled": False}
        return await original(self, command, **parameters)

    PendingRuntime.request = recording  # type: ignore[method-assign]
    try:
        await bootstrap_turn()
    finally:
        PendingRuntime.request = original  # type: ignore[method-assign]

    assert "switch_session" in requests
    await adapter.cleanup_all()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [{}, {"linear": remote("grant-A")}])
async def test_the_configured_band_endpoint_survives_any_resolved_external_set(
    tmp_path: Path, monkeypatch, external
):
    overlays: list[dict[str, object]] = []

    def spawn(command, **_kwargs):
        plugin = Path(command[command.index("--plugin-dir") + 1])
        overlays.append(json.loads((plugin / ".mcp.json").read_text(encoding="utf-8")))
        return BandMcpReplyRuntime()

    ready = AsyncMock()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", spawn)
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
    band = OmpMcpServer(type="http", url="http://127.0.0.1:9/band")
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            inject_band_tools=False,
            mcp_servers={"band": band, "stale": remote("old")},
        ),
        resolve_mcp_servers=AsyncMock(return_value=external),
        emit=(),
    )
    tools = FakeAgentTools()

    await run_turn(adapter, tools)

    expected = {"band", *external}
    (overlay,) = overlays
    assert set(overlay["mcpServers"]) == expected  # the cached external entry is gone
    assert overlay["mcpServers"]["band"]["url"] == "http://127.0.0.1:9/band"
    assert set(ready.await_args.args[2]) == expected
    assert tools.turn.replied  # Band's reply tool still routes through its own server
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_settles_a_turn_queued_behind_a_blocked_resolution(
    launches: Launches, tmp_path: Path
):
    entered = asyncio.Event()
    calls = 0
    blocking = True

    async def resolve(_workspace: str):
        nonlocal calls
        calls += 1
        entered.set()
        if blocking:
            await asyncio.Event().wait()
        return {}

    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
    )
    room_lock = SignallingLock()
    adapter._locks["room-123"] = room_lock
    first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
    await asyncio.wait_for(entered.wait(), 1)
    blocking = False  # anything that resolves from here on would launch
    queued = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
    await asyncio.wait_for(room_lock.contended.wait(), 1)  # queued waits on the lock

    await asyncio.wait_for(adapter.cleanup_all(), 1)

    assert first.done() and queued.done()
    assert calls == 1 and launches.runtimes == []
    assert not adapter._workspaces.rooms
    for task in (first, queued):
        with pytest.raises(asyncio.CancelledError):
            await task
    await run_turn(adapter, FakeAgentTools())  # a later explicit turn recovers
    assert calls == 2 and len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_failed_native_close_keeps_the_room_owned_until_cleanup_succeeds(
    launches: Launches, tmp_path: Path
):
    shared = str(tmp_path)
    adapter = OmpAdapter(workspace_for_room=lambda _room: shared, emit=())
    await run_turn(adapter, FakeAgentTools())
    (retiring,) = launches.runtimes
    (overlay,) = launches.plugins
    close_calls = 0

    async def close():
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise OSError("the process could not be reaped")
        retiring.closed = True

    retiring.close = close  # type: ignore[method-assign]

    with pytest.raises(OSError, match="reaped"):
        await adapter.on_cleanup("room-123")

    assert "room-123" in adapter._rooms and overlay.exists()
    with pytest.raises(ValueError):
        adapter._workspaces.claim(
            "other-room"
        )  # the workspace is still exclusively held
    await adapter.cleanup_all()
    assert close_calls == 2 and retiring.closed
    assert not adapter._rooms and not overlay.exists() and not adapter._workspaces.rooms
    await run_turn(adapter, FakeAgentTools())
    assert len(launches.runtimes) == 2  # a fresh process, never the retiring one
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_turn_after_a_failed_close_never_reuses_the_retiring_process(
    launches: Launches, tmp_path: Path
):
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    await run_turn(adapter, FakeAgentTools())
    (retiring,) = launches.runtimes
    prompts = 0
    original_prompt = retiring.prompt

    async def counted_prompt(*args, **kwargs):
        nonlocal prompts
        prompts += 1
        return await original_prompt(*args, **kwargs)

    retiring.prompt = counted_prompt  # type: ignore[method-assign]
    retiring.close = AsyncMock(side_effect=[OSError("busy"), None])  # type: ignore[method-assign]
    with pytest.raises(OSError):
        await adapter.on_cleanup("room-123")

    await run_turn(adapter, FakeAgentTools())

    assert prompts == 0  # the retiring process got no new work
    assert retiring.close.await_count == 2  # its cleanup was retried first
    assert len(launches.runtimes) == 2
    await adapter.cleanup_all()


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


@pytest.mark.asyncio
async def test_a_turn_arriving_during_retirement_is_refused(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    adapter, releases = settling_adapter(tmp_path, resolver)
    first = await start_blocked_turn(adapter, resolver)
    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)
    assert resolver.calls == 1 and launches.runtimes == []
    assert "room-123" in adapter._workspaces.rooms  # settlement still owns the claim

    resolver.settle.set()
    await asyncio.wait_for(cleanup, 1)
    with pytest.raises(asyncio.CancelledError):
        await first
    assert resolver.cancellations == 1 and releases and all(releases)
    await run_turn(adapter, FakeAgentTools())  # admission reopens after retirement
    assert resolver.calls == 2 and len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_overlapping_cleanups_cancel_the_settling_turn_once(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    adapter, releases = settling_adapter(tmp_path, resolver)
    first = await start_blocked_turn(adapter, resolver)
    room_cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)
    shutdown = asyncio.create_task(adapter.cleanup_all())
    # Shutdown must stay joined to the running retirement, not finish or re-cancel.
    done, _ = await asyncio.wait({shutdown}, timeout=0.1)

    assert not done and resolver.cancellations == 1
    assert "room-123" in adapter._workspaces.rooms
    resolver.settle.set()
    await asyncio.wait_for(asyncio.gather(room_cleanup, shutdown), 1)

    assert resolver.cancellations == 1 and resolver.settled
    assert releases and all(releases) and not adapter._workspaces.rooms
    assert launches.runtimes == [] and first.cancelled()


@pytest.mark.asyncio
async def test_cancelling_a_cleanup_waiter_leaves_retirement_running(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    adapter, releases = settling_adapter(tmp_path, resolver)
    first = await start_blocked_turn(adapter, resolver)
    waiter = asyncio.create_task(adapter.on_cleanup("room-123"))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert resolver.cancellations == 1
    assert "room-123" in adapter._workspaces.rooms
    with pytest.raises(asyncio.CancelledError):  # admission stays closed
        await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)
    resolver.settle.set()
    await asyncio.wait_for(adapter.cleanup_all(), 1)  # joins the same retirement
    assert resolver.cancellations == 1 and releases and all(releases)
    assert first.cancelled() and launches.runtimes == []


@pytest.mark.asyncio
async def test_cleanup_all_joins_a_retirement_blocked_in_native_close(
    launches: Launches, tmp_path: Path
):
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    await run_turn(adapter, FakeAgentTools())  # a settled turn leaves a live room
    (runtime,) = launches.runtimes
    closing = asyncio.Event()
    finish_close = asyncio.Event()
    closes = 0

    async def close():
        nonlocal closes
        closes += 1
        closing.set()
        await finish_close.wait()
        runtime.closed = True

    runtime.close = close  # type: ignore[method-assign]
    room_cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    await asyncio.wait_for(closing.wait(), 1)
    shutdown = asyncio.create_task(adapter.cleanup_all())
    with pytest.raises(asyncio.CancelledError):  # no new turn during teardown
        await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)

    assert not shutdown.done() and "room-123" in adapter._workspaces.rooms
    finish_close.set()
    await asyncio.wait_for(asyncio.gather(room_cleanup, shutdown), 1)

    assert closes == 1 and runtime.closed
    assert len(launches.runtimes) == 1 and not adapter._workspaces.rooms
    await run_turn(adapter, FakeAgentTools())
    assert len(launches.runtimes) == 2
    await adapter.cleanup_all()


async def not_done_within(task: asyncio.Task, seconds: float = 0.1) -> bool:
    done, _ = await asyncio.wait({task}, timeout=seconds)
    return not done


@pytest.mark.asyncio
async def test_cleanup_joins_a_turn_already_settling_an_external_cancellation(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    adapter, releases = settling_adapter(tmp_path, resolver)
    first = await start_blocked_turn(adapter, resolver)
    first.cancel()
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))

    assert await not_done_within(cleanup)
    assert resolver.cancellations == 1 and "room-123" in adapter._workspaces.rooms
    resolver.settle.set()
    await asyncio.wait_for(cleanup, 1)
    assert resolver.cancellations == 1 and releases and all(releases)
    assert first.cancelled() and launches.runtimes == []
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_during_a_deadline_settlement_keeps_the_timeout_report(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.05)
    tools = FakeAgentTools()
    first = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)  # the deadline fired

    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))

    assert await not_done_within(cleanup)
    resolver.settle.set()
    await asyncio.wait_for(cleanup, 1)
    with pytest.raises(TimeoutError):
        await first
    assert resolver.cancellations == 1 and releases and all(releases)
    assert [
        (failure["provider"], failure.get("code"))
        for failure in reported_failures(tools)
    ] == [("omp", "timeout")]
    assert launches.runtimes == []


@pytest.mark.asyncio
async def test_cleanup_requested_by_the_resolving_turn_launches_nothing(
    launches: Launches, tmp_path: Path
):
    adapter: OmpAdapter

    async def resolve(_workspace: str):
        if not launches.runtimes and resolve.calls == 0:
            resolve.calls += 1
            await adapter.on_cleanup("room-123")  # the room's own turn retires it
        return {"linear": remote("late")}

    resolve.calls = 0  # type: ignore[attr-defined]
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)
    await asyncio.wait_for(adapter.cleanup_all(), 1)

    assert launches.runtimes == [] and not adapter._workspaces.rooms
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_retirement_waits_for_the_turn_not_for_its_callers_other_work(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    resolver.settle.set()  # cancellation settles immediately
    adapter, releases = settling_adapter(tmp_path, resolver)
    turn_returned = asyncio.Event()

    async def caller():
        try:
            await run_turn(adapter, FakeAgentTools())
        except asyncio.CancelledError:
            turn_returned.set()
        await asyncio.Event().wait()  # unrelated work keeps the caller alive

    wrapper = asyncio.create_task(caller())
    await asyncio.wait_for(resolver.entered.wait(), 1)

    await asyncio.wait_for(adapter.on_cleanup("room-123"), 1)

    assert turn_returned.is_set() and not wrapper.done()
    assert resolver.cancellations == 1 and releases and all(releases)
    assert not adapter._workspaces.rooms and launches.runtimes == []
    wrapper.cancel()
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_all_joins_a_retirement_with_no_room_or_turn_left(
    launches: Launches, tmp_path: Path
):
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    room_lock = asyncio.Lock()
    adapter._locks["room-123"] = room_lock
    await room_lock.acquire()  # another holder keeps the retirement from closing
    retirement = asyncio.create_task(adapter.on_cleanup("room-123"))
    await asyncio.sleep(0)  # the retirement now exists; nothing else names the room
    assert not adapter._rooms and not adapter._owners

    shutdown = asyncio.create_task(adapter.cleanup_all())

    assert await not_done_within(shutdown)  # it joined the in-flight retirement
    room_lock.release()
    await asyncio.wait_for(asyncio.gather(retirement, shutdown), 1)
    assert launches.runtimes == []


class SwallowingResolver:
    """Catches a cancellation at its first await and returns a server anyway."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        return {"linear": remote("late")}


@pytest.mark.asyncio
async def test_a_cancellation_pending_at_entry_launches_nothing(
    launches: Launches, tmp_path: Path
):
    resolver = SwallowingResolver()
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolver, emit=()
    )

    async def caller():
        asyncio.current_task().cancel()  # requested, not yet delivered
        await run_turn(adapter, FakeAgentTools())

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(caller())

    assert resolver.calls == 0 and launches.runtimes == []
    assert not adapter._workspaces.rooms and not adapter._owners
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_cancellation_handled_before_entry_does_not_stop_the_turn(
    launches: Launches, tmp_path: Path
):
    resolver = SwallowingResolver()
    adapter = OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolver, emit=()
    )

    async def caller():
        asyncio.current_task().cancel()
        try:
            await asyncio.sleep(0)  # delivered and handled by the caller itself
        except asyncio.CancelledError:
            pass
        await run_turn(adapter, FakeAgentTools())

    await asyncio.create_task(caller())

    assert resolver.calls == 1 and len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_before_the_deadline_owns_the_turn_through_slow_settlement(
    launches: Launches, tmp_path: Path
):
    resolver = SettlingResolver()
    adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.2)
    tools = FakeAgentTools()
    first = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(resolver.entered.wait(), 1)
    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

    # Settlement outlives the configured deadline; that deadline must not fire now.
    await asyncio.sleep(0.4)

    assert resolver.cancellations == 1 and not cleanup.done()
    assert "room-123" in adapter._workspaces.rooms
    resolver.settle.set()
    await asyncio.wait_for(cleanup, 1)
    with pytest.raises(asyncio.CancelledError):
        await first
    assert resolver.cancellations == 1 and releases and all(releases)
    assert not reported_failures(tools) and launches.runtimes == []
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_retirement_never_cancels_work_after_the_captured_turn_finished(
    tmp_path: Path, monkeypatch
):
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    unrelated = asyncio.Event()
    continued = asyncio.Event()
    runtime = PendingRuntime()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **k: runtime)

    async def caller():
        await run_turn(adapter, FakeAgentTools())
        await unrelated.wait()  # the caller's own work after its turn
        continued.set()

    wrapper = asyncio.create_task(caller())
    await asyncio.wait_for(runtime.yielded.wait(), 1)
    # Cleanup captures the live turn; the turn then finishes before retirement runs.
    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    runtime.finish.set()
    await asyncio.wait_for(cleanup, 1)

    assert not wrapper.cancelled() and not wrapper.done()
    assert runtime.closed and not adapter._workspaces.rooms
    unrelated.set()
    await asyncio.wait_for(wrapper, 1)
    assert continued.is_set()


@pytest.mark.asyncio
async def test_cleanup_joins_outcome_handling_after_the_deadline_context_exited(
    launches: Launches, tmp_path: Path, monkeypatch
):
    closing = asyncio.Event()
    finish_close = asyncio.Event()

    class FailingRuntime(PendingRuntime):
        async def prompt(self, *_args, **_kwargs):
            raise RuntimeError("native turn failed")

        async def close(self):
            closing.set()
            await finish_close.wait()
            self.closed = True

    runtime = FailingRuntime()
    monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **k: runtime)
    adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
    tools = FakeAgentTools()
    turn = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(closing.wait(), 1)  # the turn is handling its own failure

    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    assert await not_done_within(cleanup)
    finish_close.set()
    await asyncio.wait_for(cleanup, 1)

    with pytest.raises(RuntimeError, match="native turn failed"):
        await turn
    assert [failure["provider"] for failure in reported_failures(tools)] == ["omp"]
    assert runtime.closed and not adapter._workspaces.rooms


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


@pytest.mark.asyncio
async def test_self_cleanup_after_a_caller_cancellation_disarms_the_deadline(
    launches: Launches, tmp_path: Path
):
    resolver = SelfRetiringResolver()
    adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.2)
    resolver.adapter = adapter
    tools = FakeAgentTools()
    first = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(resolver.entered.wait(), 1)
    first.cancel()  # the caller cancels; the resolver then retires its own room
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)
    shutdown = asyncio.create_task(adapter.cleanup_all())

    # Settlement outlives the configured deadline; that deadline must not fire now.
    await asyncio.sleep(0.4)

    assert resolver.cancellations == 1 and not first.done() and not shutdown.done()
    assert "room-123" in adapter._workspaces.rooms
    resolver.settle.set()
    await asyncio.wait_for(shutdown, 1)
    with pytest.raises(asyncio.CancelledError):
        await first
    assert resolver.cancellations == 1 and releases and all(releases)
    assert not reported_failures(tools) and launches.runtimes == []
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_self_cleanup_after_the_deadline_keeps_the_timeout_report(
    launches: Launches, tmp_path: Path
):
    resolver = SelfRetiringResolver()
    adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.05)
    resolver.adapter = adapter
    tools = FakeAgentTools()
    first = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)  # the deadline fired first

    resolver.settle.set()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(first, 1)
    await asyncio.wait_for(adapter.cleanup_all(), 1)

    assert resolver.cancellations == 1 and resolver.settled
    assert releases and all(releases)
    assert [
        (failure["provider"], failure.get("code"))
        for failure in reported_failures(tools)
    ] == [("omp", "timeout")]
    assert launches.runtimes == []


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


@pytest.mark.asyncio
async def test_cleanup_joins_a_fired_deadline_whose_cancellation_was_consumed(
    launches: Launches, tmp_path: Path
):
    resolver = UncancellingResolver(returns=False)
    adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.05)
    tools = FakeAgentTools()
    first = asyncio.create_task(run_turn(adapter, tools))
    await asyncio.wait_for(resolver.cancel_seen.wait(), 1)  # the deadline fired

    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))

    assert await not_done_within(cleanup)
    assert resolver.cancellations == 1 and "room-123" in adapter._workspaces.rooms
    resolver.settle.set()
    await asyncio.wait_for(cleanup, 1)
    with pytest.raises(TimeoutError):
        await first
    assert resolver.cancellations == 1 and releases and all(releases)
    assert timeout_reports(tools) == [("omp", "timeout")]
    assert launches.runtimes == []


@pytest.mark.asyncio
async def test_a_result_returned_after_the_deadline_fired_launches_nothing(
    launches: Launches, tmp_path: Path
):
    resolver = UncancellingResolver(returns=True)
    resolver.settle.set()  # settlement finishes and the resolver returns a server
    adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.05)
    tools = FakeAgentTools()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(run_turn(adapter, tools), 1)

    assert resolver.cancellations == 1 and resolver.settled
    assert launches.runtimes == [] and launches.plugins == []
    assert timeout_reports(tools) == [("omp", "timeout")]
    assert not adapter._workspaces.rooms and releases and all(releases)
    await run_turn(adapter, FakeAgentTools())  # explicit recovery
    assert len(launches.runtimes) == 1
    await adapter.cleanup_all()
