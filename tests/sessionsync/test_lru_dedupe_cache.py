from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event


class TestLruDedupeCache:
    """Tests for LRU dedupe cache in _process_event()."""

    @pytest.fixture
    def ctx(self, mock_link):
        """Create ExecutionContext with mocked dependencies."""
        handler = AsyncMock()
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        # Store handler reference for assertion
        ctx._handler_mock = handler
        return ctx

    @pytest.mark.asyncio
    async def test_processed_event_added_to_cache(self, ctx):
        """Processed message events should be added to LRU cache."""
        event = make_message_event(msg_id="msg-001")

        await ctx._process_event(event)

        assert "msg-001" in ctx.claims.completed_ids(ctx.room_id)

    @pytest.mark.asyncio
    async def test_duplicate_event_skipped(self, ctx):
        """Duplicate message events should be skipped."""
        event = make_message_event(msg_id="msg-001")

        # Process first time
        await ctx._process_event(event)
        assert ctx._handler_mock.call_count == 1

        # Process same event again - should be skipped
        await ctx._process_event(event)
        assert ctx._handler_mock.call_count == 1  # Still 1

    @pytest.mark.asyncio
    async def test_lru_cache_evicts_oldest(self, ctx):
        """LRU cache should evict oldest entries when full."""
        limit = ctx.claims.max_completed

        for i in range(limit):
            event = make_message_event(msg_id=f"msg-{i:03d}")
            await ctx._process_event(event)

        assert len(ctx.claims.completed_ids(ctx.room_id)) == limit
        assert "msg-000" in ctx.claims.completed_ids(ctx.room_id)

        event_overflow = make_message_event(msg_id=f"msg-{limit:03d}")
        await ctx._process_event(event_overflow)

        assert len(ctx.claims.completed_ids(ctx.room_id)) == limit
        assert "msg-000" not in ctx.claims.completed_ids(ctx.room_id)
        assert f"msg-{limit:03d}" in ctx.claims.completed_ids(ctx.room_id)

    @pytest.mark.asyncio
    async def test_duplicate_refreshes_lru_position(self, ctx):
        """Accessing duplicate should refresh its LRU position."""
        limit = ctx.claims.max_completed

        for i in range(limit):
            event = make_message_event(msg_id=f"msg-{i:03d}")
            await ctx._process_event(event)

        event0_dup = make_message_event(msg_id="msg-000")
        await ctx._process_event(event0_dup)

        event_overflow = make_message_event(msg_id=f"msg-{limit:03d}")
        await ctx._process_event(event_overflow)

        assert "msg-000" in ctx.claims.completed_ids(ctx.room_id)
        assert "msg-001" not in ctx.claims.completed_ids(ctx.room_id)
