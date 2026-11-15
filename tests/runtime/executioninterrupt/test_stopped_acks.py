"""Acknowledgements refused while the room is stopped."""

from __future__ import annotations

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestStoppedAcks:
    """A refused acknowledgement retries only the ack, beyond the retry budget."""

    async def test_stopped_ack_retries_only_ack_after_play_beyond_budget(
        self, mock_link
    ) -> None:
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        mock_link.mark_processed.side_effect = RoomExecutionStoppedError(ctx.room_id)
        assert (
            await attempt_delivery(ctx, make_message_event(msg_id="ack"))
            is TurnOutcome.RETRY_LATER
        )
        for _ in range(ctx.config.max_message_retries + 3):
            assert (
                await attempt_delivery(ctx, backlog_message("ack"))
                is TurnOutcome.RETRY_LATER
            )
            assert ctx.claims.is_ack_pending(ctx.room_id, "ack")
            assert not ctx.claims.is_completed(ctx.room_id, "ack")
        mock_link.mark_failed.assert_not_awaited()
        mock_link.mark_processed.side_effect = None
        assert (
            await attempt_delivery(ctx, backlog_message("ack")) is TurnOutcome.ADVANCED
        )
        assert handler.completed == ["ack"]
        assert ctx.claims.is_completed(ctx.room_id, "ack")
