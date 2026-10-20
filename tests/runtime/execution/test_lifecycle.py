from __future__ import annotations

from band.runtime.execution import (
    ExecutionContext,
)


class TestExecutionContextLifecycle:
    """Test ExecutionContext start/stop lifecycle."""

    async def test_start_creates_task(self, mock_link, mock_handler):
        """start() should create processing task."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        assert ctx.is_running is True
        assert ctx._process_loop_task is not None

        # Cleanup
        await ctx.stop()

    async def test_start_idempotent(self, mock_link, mock_handler):
        """start() twice should not create duplicate tasks."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()
        task1 = ctx._process_loop_task
        await ctx.start()  # Second call
        task2 = ctx._process_loop_task

        assert task1 is task2

        await ctx.stop()

    async def test_stop_cancels_task(self, mock_link, mock_handler):
        """stop() should cancel task and clear it."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()
        await ctx.stop()

        assert ctx.is_running is False
        assert ctx._process_loop_task is None

    async def test_stop_when_not_started_is_noop(self, mock_link, mock_handler):
        """stop() when not started should be safe."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.stop()  # Should not raise

        assert ctx.is_running is False
