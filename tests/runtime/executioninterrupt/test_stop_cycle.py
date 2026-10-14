"""Stopping an in-flight reasoning cycle."""

from __future__ import annotations

import asyncio

from band.runtime.execution import ExecutionContext, TurnOutcome
from tests.conftest import BlockingHandler, make_message_event


class TestStopInFlightCycle:
    async def test_stop_leaves_message_actionable(self, mock_link):
        """Stop aborts the cycle but leaves the message in 'processing' (no
        mark_processed, not remembered) so the platform replays it on play."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="s1")))
        await handler.started.wait()

        ctx.interrupt(kind="stop")
        result = await proc

        assert result is TurnOutcome.PAUSED
        mock_link.mark_processed.assert_not_awaited()
        assert "s1" not in ctx.claims.completed_ids(ctx.room_id)
        # Local in-flight claim released so it can be reprocessed on play.
        assert "s1" not in ctx.claims.inflight_ids(ctx.room_id)
