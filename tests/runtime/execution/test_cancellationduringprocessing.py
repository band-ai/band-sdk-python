from __future__ import annotations

import asyncio

from band.runtime.execution import (
    ExecutionContext,
)
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
)
from tests.runtime.conftest import wait_for_condition


class TestCancellationDuringProcessing:
    """Tests for cancellation during message processing."""

    async def test_stop_cancels_slow_processing(self, mock_link):
        """stop() should cancel processing and release the in-flight claim."""

        async def slow_handler(ctx, event):
            await asyncio.sleep(10)  # Would take 10 seconds

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()

        # Wait for sync to complete
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        # Enqueue a message to trigger processing
        event = make_message_event(room_id="room-123", msg_id="msg-001", content="Test")
        await ctx.on_event(event)

        # Wait for the slow handler to actually be in flight
        await wait_for_condition(lambda: ctx.is_processing)

        # Stop should cancel processing
        start = asyncio.get_running_loop().time()
        await ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete quickly (not wait 10 seconds for handler)
        assert elapsed < 1.0, f"stop() took {elapsed}s - should cancel processing"

        # Cancellation must release the local in-flight claim
        assert ctx.claims.inflight_ids(ctx.room_id) == []
