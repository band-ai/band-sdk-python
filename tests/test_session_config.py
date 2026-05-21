"""
Session configuration tests - verify enable_context_hydration behavior.

Tests cover:
- SessionConfig defaults
- get_history_for_llm() behavior when hydration disabled
- Event processing skips hydration when disabled
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from thenvoi.runtime.types import SessionConfig
from thenvoi.runtime.execution import ExecutionContext

# Import test helpers from conftest
from tests.conftest import make_message_event


class TestSessionConfigDefaults:
    """Verify SessionConfig default values."""

    def test_default_enable_context_hydration_is_true(self):
        """Default should enable context hydration for backward compatibility."""
        config = SessionConfig()
        assert config.enable_context_hydration is True

    def test_default_enable_context_cache_is_true(self):
        """Default should enable context caching."""
        config = SessionConfig()
        assert config.enable_context_cache is True

    def test_can_disable_context_hydration(self):
        """Should be able to explicitly disable context hydration."""
        config = SessionConfig(enable_context_hydration=False)
        assert config.enable_context_hydration is False


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
        mock_msg1.inserted_at = datetime.now(timezone.utc).isoformat()

        mock_msg2 = MagicMock()
        mock_msg2.id = "msg-2"
        mock_msg2.content = "Hi there!"
        mock_msg2.sender_id = "agent-1"
        mock_msg2.sender_type = "Agent"
        mock_msg2.sender_name = "TestBot"
        mock_msg2.message_type = "text"
        mock_msg2.inserted_at = datetime.now(timezone.utc).isoformat()

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
