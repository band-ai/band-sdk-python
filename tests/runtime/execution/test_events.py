"""ExecutionContextEvents behavior."""

from __future__ import annotations

from band.runtime.execution import (
    ExecutionContext,
)
from tests.conftest import (
    make_message_event,
)
from tests.runtime.conftest import wait_for_condition


class TestExecutionContextEvents:
    """Test ExecutionContext event handling."""

    async def test_on_event_enqueues(self, mock_link, mock_handler):
        """on_event() should add event to queue."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        await ctx.on_event(event)

        assert ctx.queue.qsize() == 1

    async def test_processes_message_event(self, mock_link, mock_handler):
        """Should process message events through handler."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        event = make_message_event(room_id="room-123", msg_id="msg-1", content="Hello")
        await ctx.on_event(event)

        await wait_for_condition(lambda: mock_handler.call_count >= 1)

        mock_handler.assert_called()
        call_args = mock_handler.call_args[0]
        assert call_args[0] is ctx
        assert call_args[1].type == "message_created"

        await ctx.stop()

    async def test_deduplicates_messages(self, mock_link, mock_handler):
        """Should skip duplicate message IDs."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        event = make_message_event(room_id="room-123", msg_id="msg-1")

        # Send same message twice
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.call_count >= 1)
        await ctx.on_event(event)
        await wait_for_condition(lambda: ctx.queue.qsize() == 0)

        # Should only be called once
        assert mock_handler.call_count == 1

        await ctx.stop()
