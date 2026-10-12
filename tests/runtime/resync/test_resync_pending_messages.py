"""Tests for ExecutionContext._resync_pending_messages()."""

from __future__ import annotations

from band.runtime.execution import ExecutionContext
from tests.runtime.conftest import wait_for_condition
from tests.runtime.resync.kit import make_platform_message


class TestResyncPendingMessages:
    """Tests for ExecutionContext._resync_pending_messages()."""

    async def test_empty_returns_immediately(self, mock_link, mock_handler):
        """/next returning None immediately should not call the handler."""
        mock_link.get_next_message.return_value = None

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Call directly without starting the loop
        await ctx._resync_pending_messages()

        mock_handler.assert_not_called()

    async def test_processes_single_missed_message(self, mock_link, mock_handler):
        """/next returning one message then None should process that message."""
        msg = make_platform_message(msg_id="missed-1", room_id="room-1")
        # First call returns the missed message, second returns None
        mock_link.get_next_message.side_effect = [msg, None]

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        # Reset state so _resync_pending_messages runs fresh
        mock_link.get_next_message.side_effect = [msg, None]
        await ctx._resync_pending_messages()

        # Handler should have been called for the missed message
        mock_handler.assert_called()

        await ctx.stop()

    async def test_skips_duplicate_message(self, mock_link, mock_handler):
        """A message already recorded as completed should be skipped."""
        msg = make_platform_message(msg_id="dup-1", room_id="room-1")
        mock_link.get_next_message.side_effect = [msg, None]

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Mark the message as already processed
        ctx.claims.remember_completed(ctx.room_id, "dup-1")

        await ctx._resync_pending_messages()

        mock_handler.assert_not_called()

    async def test_exception_does_not_propagate(self, mock_link, mock_handler):
        """Exceptions inside _resync_pending_messages should be caught, not raised."""
        mock_link.get_next_message.side_effect = RuntimeError("API down")

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Should complete without raising
        await ctx._resync_pending_messages()
