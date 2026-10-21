from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder


class TestReboundToolActivity:
    async def test_a_tool_ends_on_the_turn_it_started_on(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        listing = mock_rest_client.agent_api_participants.list_agent_chat_participants
        answer = listing.return_value

        async def held(*_args: Any, **_kwargs: Any) -> Any:
            entered.set()
            await release.wait()
            return answer

        listing.side_effect = held
        call = asyncio.create_task(tools.get_participants())
        await entered.wait()
        next_turn = ActivityRecorder()
        tools._activity_observer = next_turn
        release.set()
        await call

        recorder.assert_completed_tool(BandTool.GET_PARTICIPANTS)
        assert next_turn.events == []
