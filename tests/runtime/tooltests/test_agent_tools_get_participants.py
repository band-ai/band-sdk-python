from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.runtime.execution import ExecutionContext
from band.runtime.tools import AgentTools
from tests.conftest import make_participant_mock


class TestAgentToolsGetParticipants:
    """Test get_participants tool."""

    async def test_get_participants_success(self, mock_rest_client: MagicMock) -> None:
        """get_participants() should return Fern participant models."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_participants()

        assert len(result) == 1
        assert result[0].name == "User One"

    async def test_get_participants_empty(self, mock_rest_client: MagicMock) -> None:
        """get_participants() should return empty list if none."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=None
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.get_participants()

        assert result == []

    async def test_get_participants_updates_cache(
        self, mock_rest_client: MagicMock
    ) -> None:
        """get_participants() should refresh self._participants for mention resolution."""
        tools = AgentTools("room-123", mock_rest_client)
        assert tools._participants == []

        await tools.get_participants()

        assert tools._participants == [
            {
                "id": "user-1",
                "name": "User One",
                "type": "User",
                "handle": "user-one",
                "description": None,
            }
        ]

    async def test_get_participants_preserves_cache_when_data_none(
        self, mock_rest_client: MagicMock
    ) -> None:
        """``data is None`` indicates a transient/unexpected response — the
        cache should be preserved rather than wiped (an agent should always
        be a participant in its own room)."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=None
        )
        cached = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
        ]
        tools = AgentTools("room-123", mock_rest_client, participants=cached)

        result = await tools.get_participants()

        assert result == []
        assert tools._participants == cached

    async def test_get_participants_clears_cache_on_empty_list(
        self, mock_rest_client: MagicMock
    ) -> None:
        """An explicit empty list from the server is authoritative — cache should clear."""
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=[]
        )
        stale = [{"id": "ghost", "name": "Ghost", "type": "User", "handle": "ghost"}]
        tools = AgentTools("room-123", mock_rest_client, participants=stale)

        await tools.get_participants()

        assert tools._participants == []

    async def test_get_participants_syncs_roster_to_ctx(
        self, mock_rest_client: MagicMock
    ) -> None:
        """get_participants() must make the ctx roster follow the REST list —
        stale entries drop (even ctx-only ones this AgentTools never saw) and
        new ones appear — so the refresh survives turn boundaries."""

        ctx = ExecutionContext(
            room_id="room-123",
            link=MagicMock(rest=mock_rest_client),
            on_execute=AsyncMock(),
        )
        ctx.add_participant(
            {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
        )
        tools = AgentTools.from_context(ctx)
        # A ctx-only participant that joined after tools were built, then left
        # before the refresh — it must not survive as a ghost.
        ctx.add_participant({"id": "user-3", "name": "User Three", "type": "User"})

        # Server returns only a *new* participant — user-1 and user-3 are gone.
        new_p = make_participant_mock("user-2", "User Two", "User", handle="user-two")
        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(return_value=MagicMock(data=[new_p]))
        )

        await tools.get_participants()

        assert [p["id"] for p in ctx.participants] == ["user-2"]
        assert ctx.participants[0]["handle"] == "user-two"

    async def test_send_message_mentions_newly_discovered_participant(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Mentioning a participant first seen via get_participants() should not raise."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.get_participants()
        await tools.send_message("Hi @user-one!", mentions=["user-one"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert len(message.mentions) == 1
        assert message.mentions[0].id == "user-1"
        assert message.mentions[0].handle == "user-one"
