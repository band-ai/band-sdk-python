"""Telling shutdown cancellation apart from an interrupt."""

from __future__ import annotations

import asyncio

from band.runtime.execution import ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    assert_fresh_cycle_still_propagates_shutdown_cancel,
)


class TestShutdownVsInterrupt:
    async def test_swallowed_interrupt_does_not_leak_into_shutdown(self, mock_link):
        """A handler swallowing cancellation must not leave the control kind set."""
        first_started = asyncio.Event()

        async def swallow_cancel(ctx, event):
            first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

        ctx = ExecutionContext(
            "room-123", mock_link, swallow_cancel, agent_id="agent-123"
        )
        first = asyncio.create_task(
            ctx._run_cycle(make_message_event(msg_id="swallow"), "swallow")
        )
        await first_started.wait()

        assert ctx.interrupt() is True
        assert await first is True
        assert ctx._interrupt_kind is None

        await assert_fresh_cycle_still_propagates_shutdown_cancel(ctx, "shutdown")

    async def test_shutdown_cancels_cycle_without_marking(self, mock_link):
        """stop() (shutdown) cancels an in-flight cycle, does NOT mark it
        processed, and leaves no orphaned child task."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        await ctx.start()

        # Feed a message through the running loop.
        await ctx.on_event(make_message_event(msg_id="sd1"))
        await handler.started.wait()

        cycle_task = ctx._active_cycle_task
        assert cycle_task is not None

        graceful = await ctx.stop()
        assert graceful is True
        mock_link.mark_processed.assert_not_awaited()  # shutdown != consume
        assert cycle_task.cancelled() or cycle_task.done()
        assert ctx._active_cycle_task is None
