"""Static and runtime checks that published AgentBackend contracts hold."""

from __future__ import annotations

import pytest

from band import (
    AgentBackend,
    RunResult,
)
from band.core.backends.native import NativeToolLoopBackend
from band.core.backends.observing import ObservingTools
from band.core.contracts import ModelRequest, ModelResponse
from band.core.protocols import ModelContext
from band.core.run.cancellation import NeverCancelled
from band.core.run.context import SimpleRunContext
from band.runtime.tools import ToolCallOutcome
from band.testing import FakeAgentTools
from tests.core.adapterhelpers import (
    RecordingAdapter,
    make_agent_input,
    make_platform_message,
)


@pytest.mark.asyncio
async def test_adapter_turn_satisfies_run_contract() -> None:
    from band.core.backends.oneshot import run_adapter_turn

    adapter = RecordingAdapter()
    tools = FakeAgentTools(room_id="room-1")
    inp = make_agent_input("hi", tools=tools, room_id="room-1")
    result = await run_adapter_turn(
        adapter,
        inp,
        context=SimpleRunContext(tools=tools, cancellation=NeverCancelled()),
    )
    assert isinstance(result, RunResult)
    assert result.delivery is None
    assert adapter.calls[0]["msg"].content == "hi"


@pytest.mark.asyncio
async def test_observing_tools_soft_fail_returns_error_string() -> None:
    """Shim must preserve AgentTools soft-fail semantics for adapter LLM loops."""

    class SoftFailTools(FakeAgentTools):
        async def execute_tool_call_structured(
            self, tool_name: str, arguments: dict
        ) -> ToolCallOutcome:
            return ToolCallOutcome(
                value="Invalid arguments for band_send_message: content: required",
                ok=False,
                error_message="validation failed",
            )

    observing = ObservingTools(_inner=SoftFailTools(room_id="room-1"))
    result = await observing.execute_tool_call("band_send_message", {})
    assert isinstance(result, str)
    assert "Invalid arguments" in result
    assert observing.receipt is None


class EchoProvider:
    def default_history_policy(self):
        from band.core.backends.history import DefaultHistoryPolicy

        return DefaultHistoryPolicy()

    async def complete(
        self, request: ModelRequest, *, context: ModelContext
    ) -> ModelResponse:
        return ModelResponse(text="ok", stop_reason="end_turn")


@pytest.mark.asyncio
async def test_native_backend_run_returns_text_output() -> None:
    backend = NativeToolLoopBackend(provider=EchoProvider())
    tools = FakeAgentTools(room_id="room-1")
    result = await backend.run(
        session_id="room-1",
        message=make_platform_message("ping"),
        context=SimpleRunContext(tools=tools, cancellation=NeverCancelled()),
    )
    assert result.text == "ok"
