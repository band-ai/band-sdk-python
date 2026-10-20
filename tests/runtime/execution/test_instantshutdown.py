from __future__ import annotations

import asyncio

from band.runtime.execution import (
    ExecutionContext,
)


class TestInstantShutdown:
    """Tests for instant cancellation without timeout waiting."""

    async def test_stop_returns_quickly_when_idle(self, mock_link, mock_handler):
        """stop() should return quickly even when waiting on empty queue."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()

        # Give loop time to reach queue.get()
        await asyncio.sleep(0.01)

        # Stop should be instant (no 60-second timeout)
        start = asyncio.get_running_loop().time()
        await ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete in well under 1 second
        assert elapsed < 0.5, f"stop() took {elapsed}s - should be instant"

    async def test_stop_is_idempotent(self, mock_link, mock_handler):
        """Multiple stop() calls should be safe."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()
        await ctx.stop()
        await ctx.stop()  # Should not raise
        await ctx.stop()  # Should not raise

        assert ctx.is_running is False

    async def test_stop_before_start_is_safe(self, mock_link, mock_handler):
        """stop() without start() should be safe."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.stop()  # Should not raise
        assert ctx.is_running is False
