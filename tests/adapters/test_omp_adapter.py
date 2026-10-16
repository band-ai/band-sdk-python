"""Native OMP turn ownership and tool outcome regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.core.protocols import TurnResultAlreadyReported
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
