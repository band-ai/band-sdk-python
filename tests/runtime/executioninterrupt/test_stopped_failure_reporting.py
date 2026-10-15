"""Failure reporting refused while the room is stopped."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.runtime.scheduler.driver import attempt_delivery


class TestStoppedFailureReporting:
    """A refused failure report never breaks the room loop."""

    @pytest.mark.parametrize("refusal", ["mark", "post"])
    async def test_stopped_failure_reporting_preserves_room_loop(
        self, mock_link, refusal: str
    ) -> None:
        first = True

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            nonlocal first
            if first:
                first = False
                raise RuntimeError("provider failed")

        ctx = ExecutionContext("room-123", mock_link, handler)
        if refusal == "mark":
            mock_link.mark_failed.side_effect = RoomExecutionStoppedError(ctx.room_id)
        else:
            mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
                side_effect=RoomExecutionStoppedError(ctx.room_id)
            )
        expected = (
            TurnOutcome.RETRY_LATER if refusal == "mark" else TurnOutcome.ADVANCED
        )
        assert (
            await attempt_delivery(ctx, make_message_event(msg_id="failure"))
            is expected
        )
        assert ctx.claims.is_ack_pending(ctx.room_id, "failure") is (refusal == "mark")
        assert (
            await attempt_delivery(ctx, make_message_event(msg_id="next"))
            is TurnOutcome.ADVANCED
        )
        mock_link.mark_processed.assert_awaited_once_with("room-123", "next")
