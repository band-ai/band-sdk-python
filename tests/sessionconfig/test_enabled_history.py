from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig


class TestGetHistoryForLLMHydrationEnabled:
    """Test get_history_for_llm() when hydration is enabled (default)."""

    @pytest.fixture
    def mock_link(self):
        """Create a mock BandLink with context data."""
        link = MagicMock()
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Mock context response
        mock_msg1 = MagicMock()
        mock_msg1.id = "msg-1"
        mock_msg1.content = "Hello"
        mock_msg1.sender_id = "user-1"
        mock_msg1.sender_type = "User"
        mock_msg1.sender_name = "Alice"
        mock_msg1.message_type = "text"
        mock_msg1.inserted_at = datetime.now(UTC).isoformat()

        mock_msg2 = MagicMock()
        mock_msg2.id = "msg-2"
        mock_msg2.content = "Hi there!"
        mock_msg2.sender_id = "agent-1"
        mock_msg2.sender_type = "Agent"
        mock_msg2.sender_name = "TestBot"
        mock_msg2.message_type = "text"
        mock_msg2.inserted_at = datetime.now(UTC).isoformat()

        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[mock_msg1, mock_msg2])
        )
        link.get_next_message = AsyncMock(return_value=None)
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        return link

    @pytest.fixture
    def dummy_handler(self):
        """Create a dummy execution handler."""

        async def handler(ctx, event):
            pass

        return handler

    @pytest.mark.asyncio
    async def test_fetches_and_returns_history_when_enabled(
        self, mock_link, dummy_handler
    ):
        """Should fetch and return history when enable_context_hydration is True."""
        config = SessionConfig(enable_context_hydration=True)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=dummy_handler,
            config=config,
        )

        # Hydrate context first
        await ctx.hydrate()
        history = ctx.get_history_for_llm()

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hi there!"
        # Should have called get_agent_chat_context
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_called_once()
