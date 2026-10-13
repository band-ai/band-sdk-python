"""Control signals arriving inside the claim window."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from band.runtime.execution import BacklogProcessResult, ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)


class TestControlSignalInClaimWindow:
    """Signals landing after a message is claimed but before its cancellable
    cycle task exists (the mark_processing/hydration window) must not be lost.
    """

    @staticmethod
    def _gated_mark_processing(
        claiming: asyncio.Event, release: asyncio.Event
    ) -> AsyncMock:
        """mark_processing that parks inside the claim so a test can fire a
        signal while the cycle task does not yet exist."""

        async def _gate(room_id: str, msg_id: str) -> bool:
            claiming.set()
            await release.wait()
            return True

        return AsyncMock(side_effect=_gate)

    async def test_interrupt_in_window_aborts_before_handler(self, mock_link):
        claiming, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processing = self._gated_mark_processing(claiming, release)
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="w1")))
        await claiming.wait()

        # No cycle task to cancel yet, but the signal must still take effect.
        assert ctx._active_cycle_task is None
        assert ctx.interrupt() is True

        release.set()
        result = await proc

        assert result is True
        assert handler.invoked == []  # handler never ran
        mock_link.mark_processed.assert_awaited_once_with("room-123", "w1")  # consumed
        assert "w1" in ctx.claims.completed_ids(ctx.room_id)

    async def test_stop_in_window_leaves_message_actionable(self, mock_link):
        claiming, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processing = self._gated_mark_processing(claiming, release)
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="w2")))
        await claiming.wait()

        ctx.stop_room()  # sets _stopped and records the pending stop

        release.set()
        result = await proc

        assert result is True
        assert handler.invoked == []
        assert ctx._stopped is True
        mock_link.mark_processed.assert_not_awaited()  # left for replay on play
        assert "w2" not in ctx.claims.completed_ids(ctx.room_id)

    async def test_interrupt_in_backlog_window_aborts_and_advances(self, mock_link):
        claiming, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processing = self._gated_mark_processing(claiming, release)
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(ctx._process_backlog_message(backlog_message("bw1")))
        await claiming.wait()

        assert ctx.interrupt() is True

        release.set()
        result = await proc

        assert result == BacklogProcessResult.ADVANCED
        assert handler.invoked == []
        mock_link.mark_processed.assert_awaited_once_with("room-123", "bw1")

    async def test_interrupt_between_cycles_arms_nothing(self, mock_link):
        """A truly idle interrupt (no claim in flight) stays a no-op and does not
        arm a pending signal that would mis-flag the next message."""
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        assert ctx.interrupt() is False

        # The next message runs to completion, unaffected.
        result = await ctx._process_event(make_message_event(msg_id="n1"))
        assert result is True
        assert handler.completed == ["n1"]
