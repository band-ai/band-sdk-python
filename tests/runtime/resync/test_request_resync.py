"""Tests for ExecutionContext.request_resync()."""

from __future__ import annotations

from band.runtime.execution import ExecutionContext
from band.runtime.scheduler import ResyncRequest
from tests.runtime.conftest import wait_for_condition


class TestRequestResync:
    """Tests for ExecutionContext.request_resync()."""

    async def test_enqueues_resync_sentinel(self, mock_link, mock_handler):
        """request_resync() should put a ResyncRequest onto the queue."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)

        await ctx.request_resync()

        assert ctx.queue.qsize() == 1
        item = ctx.queue.get_nowait()
        assert isinstance(item, ResyncRequest)

    async def test_sentinel_triggers_resync(self, mock_link, mock_handler):
        """Enqueueing a sentinel should cause the Phase 2 loop to call /next."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()

        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        call_count_before = mock_link.get_next_message.call_count

        await ctx.request_resync()
        await wait_for_condition(
            lambda: mock_link.get_next_message.call_count > call_count_before
        )

        # At least one more /next call should have occurred
        assert mock_link.get_next_message.call_count > call_count_before

        await ctx.stop()

    async def test_multiple_resyncs_dont_crash(self, mock_link, mock_handler):
        """Multiple rapid request_resync() calls should not crash or deadlock."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        for _ in range(5):
            await ctx.request_resync()

        await wait_for_condition(lambda: ctx.queue.qsize() == 0)
        # Still running, no exception
        assert ctx.is_running

        await ctx.stop()
