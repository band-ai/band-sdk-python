from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig


class TestGetHistoryForLLMHydrationDisabled:
    """Test get_history_for_llm() when hydration is disabled."""

    @pytest.fixture
    def mock_link(self):
        """Create a mock BandLink."""
        link = MagicMock()
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
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

    def test_returns_empty_when_hydration_disabled(self, mock_link, dummy_handler):
        """Should return empty list when enable_context_hydration is False."""
        config = SessionConfig(enable_context_hydration=False)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=dummy_handler,
            config=config,
        )

        history = ctx.get_history_for_llm()

        assert history == []

    def test_returns_empty_with_exclude_id_when_hydration_disabled(
        self, mock_link, dummy_handler
    ):
        """Should return empty list even with exclude_message_id parameter."""
        config = SessionConfig(enable_context_hydration=False)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=dummy_handler,
            config=config,
        )

        history = ctx.get_history_for_llm(exclude_message_id="msg-456")

        assert history == []
