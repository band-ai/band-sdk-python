"""Regression tests for CrewAIFlowSubCrewReporter error propagation.

When the centralized ``AgentTools.send_message`` rejects a sub-Crew visible
send (e.g. missing mentions), the reporter must propagate the original
``BandToolError`` so the LLM learns the real cause, instead of wrapping it in
the generic "send_message failed for sub-Crew side effect" message.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch: pytest.MonkeyPatch):
    fake = MagicMock()
    fake_flow_module = MagicMock()
    fake_flow_module.Flow = type("Flow", (), {})
    fake.flow = MagicMock()
    fake.flow.flow = fake_flow_module
    monkeypatch.setitem(sys.modules, "crewai", fake)
    monkeypatch.setitem(sys.modules, "crewai.flow", fake.flow)
    monkeypatch.setitem(sys.modules, "crewai.flow.flow", fake_flow_module)
    yield


from band.adapters.crewai_flow import (  # noqa: E402
    CrewAIFlowSubCrewReporter,
    SideEffectExecutor,
)
from band.converters.crewai_flow import (  # noqa: E402
    CrewAIFlowJoinPolicy,
    CrewAIFlowSessionState,
    CrewAIFlowTextOnlyBehavior,
)
from band.core.exceptions import BandToolError  # noqa: E402
from band.runtime.tools import SEND_MESSAGE_REQUIRES_MENTION_MESSAGE  # noqa: E402
from band.testing.fake_tools import FakeAgentTools  # noqa: E402


def _make_reporter(tools: Any) -> CrewAIFlowSubCrewReporter:
    executor = SideEffectExecutor(
        tools=tools,
        room_id="room-1",
        run_id="run-1",
        parent_message_id="msg-parent",
        metadata_namespace="band_crewai_flow",
        join_policy=CrewAIFlowJoinPolicy.ALL,
        text_only_behavior=CrewAIFlowTextOnlyBehavior.ERROR_EVENT,
    )
    return CrewAIFlowSubCrewReporter(
        executor=executor,
        run_id="run-1",
        state=CrewAIFlowSessionState(),
    )


@pytest.mark.asyncio
async def test_propagates_band_tool_error_from_send_message() -> None:
    """The reporter re-raises the original mention BandToolError verbatim."""
    tools = FakeAgentTools()
    tools.send_message = AsyncMock(  # type: ignore[method-assign]
        side_effect=BandToolError(SEND_MESSAGE_REQUIRES_MENTION_MESSAGE)
    )
    reporter = _make_reporter(tools)

    with pytest.raises(BandToolError) as exc_info:
        await reporter.execute_send_message(tools, "Hello", [])

    assert str(exc_info.value) == SEND_MESSAGE_REQUIRES_MENTION_MESSAGE
    assert reporter._executor.side_effect_aborted is True


@pytest.mark.asyncio
async def test_wraps_unexpected_send_message_failure() -> None:
    """Non-BandToolError failures still surface the generic wrapper message."""
    tools = FakeAgentTools()
    tools.send_message = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("network down")
    )
    reporter = _make_reporter(tools)

    with pytest.raises(BandToolError, match="send_message failed for sub-Crew"):
        await reporter.execute_send_message(tools, "Hello", ["@alice"])

    assert reporter._executor.side_effect_aborted is True
