from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event


class TestProcessEventHydration:
    """Test _process_event() hydration behavior."""

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
    def sample_event(self):
        """Create a sample platform event."""
        return make_message_event(
            room_id="room-123",
            msg_id="msg-123",
            content="Hello",
            sender_id="user-456",
            sender_type="User",
        )

    @pytest.mark.asyncio
    async def test_skips_hydration_when_disabled(self, mock_link, sample_event):
        """Should NOT call hydrate() when enable_context_hydration is False."""
        handler_called = False

        async def track_handler(ctx, event):
            nonlocal handler_called
            handler_called = True

        config = SessionConfig(enable_context_hydration=False)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=track_handler,
            config=config,
        )

        await ctx._process_event(sample_event)

        assert handler_called
        # Should NOT have fetched context
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_hydrates_context_when_enabled(self, mock_link, sample_event):
        """Should call hydrate() when enable_context_hydration is True."""
        handler_called = False

        async def track_handler(ctx, event):
            nonlocal handler_called
            handler_called = True

        config = SessionConfig(enable_context_hydration=True)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=track_handler,
            config=config,
        )

        await ctx._process_event(sample_event)

        assert handler_called
        # Should have fetched context
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_called_once()
