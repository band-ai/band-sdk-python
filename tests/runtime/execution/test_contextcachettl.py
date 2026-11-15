from __future__ import annotations

from datetime import UTC, datetime, timedelta

from band.runtime.context import ConversationContext
from band.runtime.execution import (
    ExecutionContext,
)
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
)
from tests.runtime.conftest import wait_for_condition


class TestContextCacheTTL:
    """Tests for context cache TTL expiry."""

    async def test_get_context_rehydrates_when_cache_is_expired(
        self, mock_link, mock_handler
    ):
        """Expired cache should be invalidated and rehydrated."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=300),
        )

        await ctx.get_context()
        mock_link.rest.agent_api_context.get_agent_chat_context.reset_mock()

        ctx._context_cache = ConversationContext(
            room_id="room-123",
            messages=[{"id": "stale-msg"}],
            participants=ctx.participants,
            hydrated_at=datetime.now(UTC) - timedelta(seconds=301),
        )
        ctx._context_hydrated = True

        context = await ctx.get_context()

        mock_link.rest.agent_api_context.get_agent_chat_context.assert_awaited_once()
        assert len(context.messages) == 1
        assert context.messages[0]["id"] == "msg-1"

    async def test_get_history_for_llm_invalidates_expired_cache(
        self, mock_link, mock_handler
    ):
        """Synchronous history access should never return stale cached data."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=300),
        )

        await ctx.get_context()
        ctx._context_cache = ConversationContext(
            room_id="room-123",
            messages=[{"id": "stale-msg", "content": "stale"}],
            participants=ctx.participants,
            hydrated_at=datetime.now(UTC) - timedelta(seconds=301),
        )
        ctx._context_hydrated = True

        history = ctx.get_history_for_llm()

        assert history == []
        assert ctx._context_cache is None
        assert ctx._context_hydrated is False

    async def test_zero_ttl_forces_immediate_refresh(self, mock_link, mock_handler):
        """TTL=0 should force rehydration on the next access."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=0),
        )

        await ctx.get_context()
        mock_link.rest.agent_api_context.get_agent_chat_context.reset_mock()

        await ctx.get_context()

        mock_link.rest.agent_api_context.get_agent_chat_context.assert_awaited_once()

    async def test_processing_rehydrates_expired_cache_before_handler(
        self, mock_link, mock_handler
    ):
        """Message processing should refresh expired cache before the handler runs."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=300),
        )

        ctx._context_cache = ConversationContext(
            room_id="room-123",
            messages=[{"id": "stale-msg"}],
            participants=[],
            hydrated_at=datetime.now(UTC) - timedelta(seconds=301),
        )
        ctx._context_hydrated = True

        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        event = make_message_event(room_id="room-123", msg_id="msg-ttl")
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.call_count >= 1)

        mock_handler.assert_called()
        assert mock_link.rest.agent_api_context.get_agent_chat_context.await_count == 1

        await ctx.stop()
