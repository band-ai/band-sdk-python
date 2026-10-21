from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder


class TestBandToolActivity:
    async def test_a_band_tool_shows_its_name_until_it_ends(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        await tools.get_participants()
        recorder.assert_completed_tool(BandTool.GET_PARTICIPANTS)

    async def test_a_failing_band_tool_still_returns_to_reasoning(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
    ) -> None:
        participants = mock_rest_client.agent_api_participants
        participants.list_agent_chat_participants.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await tools.get_participants()
        recorder.assert_completed_tool(BandTool.GET_PARTICIPANTS)

    async def test_adapter_telemetry_is_quiet(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        await tools.send_event("private telemetry", "thought")
        assert recorder.events == []

    async def test_a_model_sent_event_shows_as_a_tool_call(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        await tools.execute_tool_call(
            BandTool.SEND_EVENT,
            {"content": "model telemetry", "message_type": "thought"},
        )
        recorder.assert_completed_tool(BandTool.SEND_EVENT)
