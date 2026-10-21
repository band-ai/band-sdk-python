from __future__ import annotations

import json

import pytest

from band.integrations.crewai.tools import (
    CrewAIToolContext,
    NoopReporter,
    build_band_crewai_tools,
)
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder

pytest.importorskip("crewai")


class TestCrewAIModelEventActivity:
    def test_a_model_sent_event_reports_once_and_is_sent_once(self) -> None:
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        built = build_band_crewai_tools(
            get_context=lambda: CrewAIToolContext(room_id="room", tools=tools),
            reporter=NoopReporter(),
        )
        send_event = next(tool for tool in built if tool.name == BandTool.SEND_EVENT)

        result = json.loads(send_event.run(content="model", message_type="thought"))

        assert result["status"] == "success"
        recorder.assert_completed_tool(BandTool.SEND_EVENT)
        assert [event["content"] for event in tools.events_sent] == ["model"]
