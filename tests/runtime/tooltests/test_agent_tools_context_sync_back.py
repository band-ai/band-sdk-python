from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.tools import AgentTools
from tests.conftest import make_participant_mock


class TestAgentToolsContextSyncBack:
    """Regression tests for AgentTools._ctx sync-back to ExecutionContext.

    Before the fix, AgentTools.from_context() copied ctx.participants (which
    returns a shallow copy via property).  Mutations to the tools instance
    (add/remove participant) never propagated back to the ExecutionContext.
    On the next turn, from_context() would copy the stale list again.
    """

    @pytest.mark.asyncio
    async def test_add_participant_syncs_back_to_ctx(
        self, mock_rest_client: MagicMock
    ) -> None:
        """add_participant() must call ctx.add_participant() with full dict."""
        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = []
        mock_ctx.hub_room_id = None
        mock_ctx.add_participant = MagicMock()

        # Override list_agent_chat_participants to return empty (avoid "already_in_room")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[]))
        )

        tools = AgentTools.from_context(mock_ctx)
        assert tools._ctx is mock_ctx

        await tools.add_participant("Agent Two")

        mock_ctx.add_participant.assert_called_once()
        added = mock_ctx.add_participant.call_args.args[0]
        assert added["id"] == "agent-2"
        assert added["name"] == "Agent Two"
        assert added["type"] == "Agent"
        assert added["handle"] == "agent-two"
        assert added["description"] == "Another agent"

    @pytest.mark.asyncio
    async def test_remove_participant_syncs_back_to_ctx(
        self, mock_rest_client: MagicMock
    ) -> None:
        """remove_participant() must call ctx.remove_participant() with correct ID."""
        participant = {
            "id": "user-1",
            "name": "User One",
            "type": "User",
            "handle": "user-one",
        }

        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = [participant]
        mock_ctx.hub_room_id = None
        mock_ctx.remove_participant = MagicMock()

        # Return same participant from REST so snapshot matches
        p_mock = make_participant_mock("user-1", "User One", "User", handle="user-one")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[p_mock]))
        )

        tools = AgentTools.from_context(mock_ctx)
        await tools.remove_participant("User One")

        mock_ctx.remove_participant.assert_called_once_with("user-1")

    @pytest.mark.asyncio
    async def test_add_participant_persists_across_recreated_tools(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Added participant must survive tools recreation via from_context().

        Uses real ExecutionContext — its ``participants`` property returns a
        copy, so without the _ctx backref the mutation would be lost.
        """

        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )

        # Empty room
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[]))
        )

        # Turn 1: add participant
        tools1 = AgentTools.from_context(ctx)
        await tools1.add_participant("Agent Two")

        assert len(ctx.participants) == 1
        assert ctx.participants[0]["id"] == "agent-2"

        # Turn 2: recreate tools — participant must still be there
        tools2 = AgentTools.from_context(ctx)
        assert len(tools2._participants) == 1
        assert tools2._participants[0]["id"] == "agent-2"

    @pytest.mark.asyncio
    async def test_remove_participant_persists_across_recreated_tools(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Removed participant must stay removed after tools recreation.

        Uses real ExecutionContext — its ``participants`` property returns a
        copy, so without the _ctx backref the removal would be lost.
        """

        participant = {
            "id": "user-1",
            "name": "User One",
            "type": "User",
            "handle": "user-one",
        }

        ctx = ExecutionContext(
            room_id="room-789",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )
        ctx.set_participants([participant])

        # REST snapshot must match ctx.participants
        p_mock = make_participant_mock("user-1", "User One", "User", handle="user-one")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[p_mock]))
        )

        # Turn 1: remove participant
        tools1 = AgentTools.from_context(ctx)
        await tools1.remove_participant("User One")

        assert len(ctx.participants) == 0

        # Turn 2: recreate tools — participant must stay removed
        tools2 = AgentTools.from_context(ctx)
        assert len(tools2._participants) == 0
