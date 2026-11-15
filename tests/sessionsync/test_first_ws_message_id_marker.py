from __future__ import annotations

import pytest

from band.runtime.execution import ExecutionContext
from tests.conftest import (
    make_message_event,
)


class TestFirstWsMessageIdMarker:
    """Tests for _first_ws_msg_id tracking in on_event()."""

    @pytest.fixture
    def ctx(self, mock_link):
        """Create ExecutionContext with mocked dependencies."""

        async def handler(ctx, event):
            pass

        return ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=handler,
        )

    @pytest.mark.asyncio
    async def test_first_event_sets_marker(self, ctx):
        """First enqueued message event should set _first_ws_msg_id."""
        assert ctx.scheduler.first_ws_id is None

        event = make_message_event(msg_id="msg-001")
        await ctx.on_event(event)

        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "msg-001"

    @pytest.mark.asyncio
    async def test_subsequent_event_does_not_change_marker(self, ctx):
        """Subsequent events should not change the marker."""
        event1 = make_message_event(msg_id="msg-001")
        event2 = make_message_event(msg_id="msg-002")
        event3 = make_message_event(msg_id="msg-003")

        await ctx.on_event(event1)
        await ctx.on_event(event2)
        await ctx.on_event(event3)

        # Should still be first message
        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "msg-001"

    @pytest.mark.asyncio
    async def test_marker_preserved_after_queue_empty(self, ctx):
        """Marker should be preserved even if queue becomes empty."""
        event = make_message_event(msg_id="msg-001")
        await ctx.on_event(event)

        # Drain the queue
        ctx.scheduler.receive(ctx.queue.get_nowait())
        assert ctx.queue.empty()

        # Marker should still be set
        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "msg-001"
