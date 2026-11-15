from __future__ import annotations

import asyncio

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

    async def test_deadline_arrival_still_delivers_a_queued_message(
        self, mock_link, mock_handler
    ):
        """A deadline that fires as an event arrives must not end the room."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.queue_wait is not None)

        task = ctx._process_loop_task
        assert task is not None
        assert ctx.scheduler.queue_wait is not None
        ctx.scheduler.queue_wait.reschedule(asyncio.get_running_loop().time())
        await ctx.on_event(make_message_event(room_id="room-123", msg_id="msg-1"))

        await wait_for_condition(lambda: mock_handler.call_count >= 1)
        assert not task.done()

        await ctx.stop()

    async def test_external_cancel_ends_the_room_task(self, mock_link, mock_handler):
        """A cancel from outside the deadline must finish the room task."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.queue_wait is not None)

        task = ctx._process_loop_task
        assert task is not None
        task.cancel()

        await wait_for_condition(task.done)
        await ctx.stop()
