from __future__ import annotations

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


class TestModelEventActivity:
    @pytest.mark.parametrize("surface", ["mcp", "pydantic"])
    async def test_model_event_reports_while_raw_telemetry_stays_quiet(
        self, surface: str
    ) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        await tools.send_event("internal", "thought")
        assert not recorder.events
        definition = TOOL_DEFINITIONS[BandTool.SEND_EVENT]
        arguments = {"content": "model", "message_type": "thought"}
        if surface == "mcp":
            await dispatch_tool(tools, definition, arguments)
        else:
            dispatch = _dispatcher(definition, definition.input_model)
            await dispatch(
                RunContext(deps=tools, model=TestModel(), usage=RunUsage()), **arguments
            )
        recorder.assert_completed_tool(BandTool.SEND_EVENT)
