from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools
from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder
from tests.adapters.activity.kit import call_over_mcp
from tests.runtime.helpers import ROOM_ID


class TestDedupedToolActivity:
    async def test_a_tool_ends_on_the_turn_it_started_on_across_an_inner_swap(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Claude's room tools swap their inner turn tools per turn; a call
        still running then must not end on the newer turn."""
        started_turn, next_turn = ActivityRecorder(), ActivityRecorder()
        room_tools = DedupingAgentTools(
            started_turn.observing(AgentTools(ROOM_ID, mock_rest_client))
        )
        entered, release = asyncio.Event(), asyncio.Event()
        listing = mock_rest_client.agent_api_participants.list_agent_chat_participants
        answer = listing.return_value

        async def held(*_args: Any, **_kwargs: Any) -> Any:
            entered.set()
            await release.wait()
            return answer

        listing.side_effect = held
        call = asyncio.create_task(
            call_over_mcp(cast(AgentTools, room_tools), BandTool.GET_PARTICIPANTS, {})
        )
        await entered.wait()
        await room_tools.update_inner(
            next_turn.observing(AgentTools(ROOM_ID, mock_rest_client))
        )
        release.set()
        await call

        started_turn.assert_completed_tool(BandTool.GET_PARTICIPANTS)
        assert next_turn.events == []
