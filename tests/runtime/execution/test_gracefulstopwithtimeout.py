from __future__ import annotations

import asyncio

from band.runtime.execution import (
    ExecutionContext,
    ExecutionState,
)
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
)
from tests.runtime.conftest import wait_for_condition


class TestGracefulStopWithTimeout:
    """Tests for graceful stop with timeout."""

    async def test_stop_returns_true_when_idle(self, mock_link, mock_handler):
        """stop() should return True when not processing."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()
        await asyncio.sleep(0.05)

        result = await ctx.stop(timeout=5.0)

        assert result is True
        assert ctx.is_running is False

    async def test_stop_returns_true_when_not_started(self, mock_link, mock_handler):
        """stop() should return True when not started."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        result = await ctx.stop(timeout=5.0)

        assert result is True

    async def test_stop_without_timeout_cancels_immediately(self, mock_link):
        """stop() without timeout should cancel immediately."""
        processing_started = asyncio.Event()

        async def slow_handler(ctx, event):
            processing_started.set()
            await asyncio.sleep(10)  # Would take 10 seconds

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        # Enqueue a message
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Wait for processing to start
        try:
            await asyncio.wait_for(processing_started.wait(), timeout=1.0)
        except TimeoutError:
            pass  # May not start if sync takes too long

        # Stop without timeout should cancel immediately
        start = asyncio.get_running_loop().time()
        await ctx.stop()  # No timeout
        elapsed = asyncio.get_running_loop().time() - start

        assert elapsed < 1.0, f"stop() took {elapsed}s - should cancel immediately"

    async def test_stop_waits_for_processing_to_complete(self, mock_link):
        """stop(timeout) should wait for current processing to complete."""
        processing_done = asyncio.Event()

        async def quick_handler(ctx, event):
            await asyncio.sleep(0.1)  # Quick processing
            processing_done.set()

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            quick_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        # Enqueue a message
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Wait for the handler to actually be in flight
        await wait_for_condition(lambda: ctx.is_processing)

        # Stop with timeout - should wait for processing
        result = await ctx.stop(timeout=5.0)

        # Should have completed gracefully
        assert result is True

    async def test_stop_returns_false_when_timeout_exceeded(self, mock_link):
        """stop(timeout) should return False and cancel the in-flight handler."""
        handler_cancelled = asyncio.Event()

        async def slow_handler(ctx, event):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                handler_cancelled.set()
                raise

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        # Enqueue a message
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Wait for the handler to actually be in flight
        await wait_for_condition(lambda: ctx.is_processing)

        result = await ctx.stop(timeout=0.1)

        # A wall-clock bound here is flaky: cancellation cleanup may legitimately
        # take up to CYCLE_CANCEL_GRACE_SECONDS on a slow runner.
        assert result is False
        assert handler_cancelled.is_set()

    async def test_wait_for_idle_returns_true_when_already_idle(
        self, mock_link, mock_handler
    ):
        """_wait_for_idle should return True immediately when idle."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.state = ExecutionState.IDLE

        result = await ctx._wait_for_idle(timeout=1.0)

        assert result is True

    async def test_wait_for_idle_returns_false_on_timeout(
        self, mock_link, mock_handler
    ):
        """_wait_for_idle should return False when timeout exceeded."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx._set_state(ExecutionState.PROCESSING)

        start = asyncio.get_running_loop().time()
        result = await ctx._wait_for_idle(timeout=0.1)
        elapsed = asyncio.get_running_loop().time() - start

        assert result is False
        # Should have waited the full timeout. Windows event-loop timers tick at
        # ~15.6 ms granularity, so the wait can end slightly early — allow one tick.
        assert elapsed >= 0.1 - 0.02
