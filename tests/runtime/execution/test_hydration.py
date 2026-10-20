from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.runtime.execution import (
    ExecutionContext,
)


class TestExecutionContextHydration:
    """Test context hydration."""

    async def test_hydrate_loads_participants(self, mock_link, mock_handler):
        """hydrate() should load participants from API."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.hydrate()

        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "User One"

    async def test_hydrate_loads_context(self, mock_link, mock_handler):
        """hydrate() should load context from API."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.hydrate()

        context = ctx.build_context()
        assert len(context.messages) == 1
        assert context.messages[0]["content"] == "Hello"

    async def test_hydrate_idempotent(self, mock_link, mock_handler):
        """hydrate() should only load once."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.hydrate()
        await ctx.hydrate()  # Second call

        # Should only call API once
        assert (
            mock_link.rest.agent_api_participants.list_agent_chat_participants.call_count
            == 1
        )

    async def test_get_context_hydrates_lazily(self, mock_link, mock_handler):
        """get_context() should hydrate lazily."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        context = await ctx.get_context()

        assert context.room_id == "room-123"
        assert len(context.messages) == 1

    async def test_get_context_force_refresh(self, mock_link, mock_handler):
        """get_context(force_refresh=True) should re-fetch context messages."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.get_context()
        await ctx.get_context(force_refresh=True)

        # Context API should be called twice
        assert mock_link.rest.agent_api_context.get_agent_chat_context.call_count == 2
        # Participants only loaded once (tracked via WebSocket, not re-fetched)
        assert (
            mock_link.rest.agent_api_participants.list_agent_chat_participants.call_count
            == 1
        )

    async def test_load_participants_empty_list_clears_roster(
        self, mock_link, mock_handler
    ):
        """response.data == [] is authoritative and empty -- it must clear a
        previously-loaded roster, not be treated as falsy/no-op."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.set_participants([{"id": "stale-user", "name": "Stale", "type": "User"}])

        mock_link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        ctx._participants_loaded = False

        result = await ctx.load_participants()

        assert result == []
        assert ctx.participants == []

    async def test_load_participants_none_data_leaves_roster_untouched(
        self, mock_link, mock_handler
    ):
        """response.data is None (a transient/unexpected response) must leave
        the previous roster in place -- unlike an authoritative empty list."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.set_participants([{"id": "kept-user", "name": "Kept", "type": "User"}])

        mock_link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=None)
        )
        ctx._participants_loaded = False

        result = await ctx.load_participants()

        assert [p["id"] for p in result] == ["kept-user"]
        assert [p["id"] for p in ctx.participants] == ["kept-user"]
