from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder
from tests.adapters.activity.kit import MODEL_CALLS, ModelCall
from tests.runtime.helpers import ROOM_ID


class TestFailedModelToolActivity:
    @pytest.mark.parametrize("call", MODEL_CALLS)
    async def test_a_failing_call_returns_the_room_to_reasoning(
        self, mock_rest_client: MagicMock, call: ModelCall
    ) -> None:
        listing = mock_rest_client.agent_api_participants.list_agent_chat_participants
        listing.side_effect = RuntimeError("room unavailable")
        recorder = ActivityRecorder()
        tools = recorder.observing(AgentTools(ROOM_ID, mock_rest_client))

        answer = await call(tools, BandTool.GET_PARTICIPANTS, {})

        assert "room unavailable" in str(answer)
        recorder.assert_completed_tool(BandTool.GET_PARTICIPANTS)
