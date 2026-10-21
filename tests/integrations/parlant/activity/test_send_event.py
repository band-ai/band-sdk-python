from __future__ import annotations

import pytest

pytest.importorskip("parlant.sdk")

from band.integrations.parlant.sessiontools import set_session_tools
from band.integrations.parlant.tools import create_parlant_tools
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.integrations.parlant.helpers import ToolServer


class TestParlantModelEventActivity:
    async def test_a_model_sent_event_reports_once_and_is_sent_once(
        self, plugin_server: ToolServer
    ) -> None:
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        set_session_tools("calling", tools)
        await plugin_server.enable(create_parlant_tools())

        await plugin_server.call(
            BandTool.SEND_EVENT,
            session_id="calling",
            arguments={"content": "model", "message_type": "thought"},
        )

        recorder.assert_completed_tool(BandTool.SEND_EVENT)
        assert [event["content"] for event in tools.events_sent] == ["model"]
