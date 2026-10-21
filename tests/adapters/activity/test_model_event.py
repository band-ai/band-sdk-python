from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from band.integrations.mcp.engine import dispatch_tool
from band.integrations.pydantic_ai.tools import _dispatcher
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools

SEND_EVENT = TOOL_DEFINITIONS[BandTool.SEND_EVENT]
ARGUMENTS = {"content": "model", "message_type": "thought"}


async def dispatch_over_mcp(tools: FakeAgentTools) -> None:
    await dispatch_tool(tools, SEND_EVENT, ARGUMENTS)


async def dispatch_over_pydantic_ai(tools: FakeAgentTools) -> None:
    dispatch = _dispatcher(SEND_EVENT, SEND_EVENT.input_model)
    await dispatch(
        RunContext(deps=tools, model=TestModel(), usage=RunUsage()), **ARGUMENTS
    )


class TestModelEventActivity:
    @pytest.mark.parametrize("dispatch", [dispatch_over_mcp, dispatch_over_pydantic_ai])
    async def test_a_model_event_reports_while_raw_telemetry_stays_quiet(
        self, dispatch: Callable[[FakeAgentTools], Awaitable[None]]
    ) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        await tools.send_event("internal", "thought")
        assert recorder.events == []
        await dispatch(tools)
        recorder.assert_completed_tool(BandTool.SEND_EVENT)
