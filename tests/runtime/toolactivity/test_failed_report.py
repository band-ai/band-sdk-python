from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from tests.runtime.helpers import ROOM_ID
from tests.runtime.toolactivity.kit import CancelledObserver, UnreachableObserver


@pytest.fixture
def unreachable() -> UnreachableObserver:
    return UnreachableObserver()


@pytest.fixture
def tools(mock_rest_client: MagicMock, unreachable: UnreachableObserver) -> AgentTools:
    return unreachable.observing(AgentTools(ROOM_ID, mock_rest_client))


class TestFailedActivityReport:
    """Activity is best-effort: a failed report never changes the tool's outcome."""

    async def test_the_tool_still_runs_and_returns_its_result(
        self,
        tools: AgentTools,
        unreachable: UnreachableObserver,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="band.runtime.toolactivity"):
            participants = await tools.get_participants()

        assert participants
        unreachable.assert_completed_tool(BandTool.GET_PARTICIPANTS)
        assert [record.levelno for record in caplog.records] == [logging.WARNING] * 2

    async def test_the_tool_keeps_its_own_failure(
        self, tools: AgentTools, mock_rest_client: MagicMock
    ) -> None:
        listing = mock_rest_client.agent_api_participants.list_agent_chat_participants
        listing.side_effect = ValueError("room gone")
        with pytest.raises(ValueError, match="room gone"):
            await tools.get_participants()

    async def test_a_cancelled_report_still_cancels_the_tool(
        self, mock_rest_client: MagicMock
    ) -> None:
        tools = CancelledObserver().observing(AgentTools(ROOM_ID, mock_rest_client))
        with pytest.raises(asyncio.CancelledError):
            await tools.get_participants()
        listing = mock_rest_client.agent_api_participants.list_agent_chat_participants
        listing.assert_not_awaited()
