from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.integrations.slack.adapter import (
    SLACK_SEND_MESSAGE_TOOL_NAME,
    SlackTeeingTools,
)
from band.integrations.slack.types import SlackRoomBinding
from band.runtime.tools import AgentTools, BandTool
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.runtime.helpers import ROOM_ID


class TestSlackTeeActivity:
    async def test_tools_called_through_the_tee_report_on_the_wrapped_turn(
        self, mock_rest_client: MagicMock
    ) -> None:
        recorder = ActivityRecorder()
        band = recorder.observing(AgentTools(ROOM_ID, mock_rest_client))
        slack = AsyncMock()
        tee = SlackTeeingTools(
            wrap=band,
            slack=slack,
            binding=SlackRoomBinding(app_slug="dev", channel="C", thread_ts="1.0"),
        )

        await tee.execute_tool_call(BandTool.GET_PARTICIPANTS, {})
        await tee.execute_tool_call(
            BandTool.SEND_EVENT, {"content": "model", "message_type": "thought"}
        )
        await tee.execute_tool_call(SLACK_SEND_MESSAGE_TOOL_NAME, {"content": "hi"})

        assert recorder.events == [
            shown_tool(BandTool.GET_PARTICIPANTS),
            SHOWN_REASONING,
            shown_tool(BandTool.SEND_EVENT),
            SHOWN_REASONING,
            shown_tool(SLACK_SEND_MESSAGE_TOOL_NAME),
            SHOWN_REASONING,
        ]
        assert slack.chat_postMessage.await_args.kwargs["text"] == "hi"
