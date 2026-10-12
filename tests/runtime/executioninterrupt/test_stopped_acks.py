"""Acknowledgements refused while the room is stopped."""

from __future__ import annotations

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.execution import BacklogProcessResult, ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)


class TestStoppedAcks:
    """A refused acknowledgement retries only the ack, beyond the retry budget."""

    async def test_stopped_ack_retries_only_ack_after_play_beyond_budget(
        self, mock_link
    ) -> None:
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        mock_link.mark_processed.side_effect = RoomExecutionStoppedError(ctx.room_id)
        assert not await ctx._process_event(make_message_event(msg_id="ack"))
        for _ in range(ctx.config.max_message_retries + 3):
            assert (
                await ctx._process_backlog_message(backlog_message("ack"))
                == BacklogProcessResult.RETRY_LATER
            )
            assert ctx.claims.is_ack_pending(ctx.room_id, "ack")
            assert not ctx.claims.is_completed(ctx.room_id, "ack")
        mock_link.mark_failed.assert_not_awaited()
        mock_link.mark_processed.side_effect = None
        assert (
            await ctx._process_backlog_message(backlog_message("ack"))
            == BacklogProcessResult.ADVANCED
        )
        assert handler.completed == ["ack"]
        assert ctx.claims.is_completed(ctx.room_id, "ack")
