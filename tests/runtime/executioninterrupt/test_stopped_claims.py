"""Claims refused or deferred while the room is stopped."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.cycle import TurnScope
from band.runtime.execution import BacklogProcessResult, ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)


class TestStoppedClaims:
    """A stopped room defers claims without invoking the handler or spending retries."""

    @pytest.mark.parametrize("path", ["live", "backlog"])
    async def test_stopped_claim_defers_without_invoking_or_poisoning_retry(
        self, mock_link, path: str
    ) -> None:
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        mock_link.mark_processing.side_effect = RoomExecutionStoppedError(ctx.room_id)
        if path == "live":
            assert not await ctx._process_event(make_message_event(msg_id="refused"))
        else:
            assert (
                await ctx._process_backlog_message(backlog_message("refused"))
                == BacklogProcessResult.RETRY_LATER
            )
        assert handler.invoked == []
        mock_link.mark_processed.assert_not_awaited()
        mock_link.mark_failed.assert_not_awaited()
        mock_link.mark_processing.side_effect = None
        assert (
            await ctx._process_backlog_message(backlog_message("refused"))
            == BacklogProcessResult.ADVANCED
        )
        assert handler.completed == ["refused"]

    async def test_pending_stop_cleanup_defers_claims_with_bounded_wait(
        self, mock_link, monkeypatch
    ) -> None:
        release, entered = asyncio.Event(), asyncio.Event()

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            entered.set()
            await release.wait()

        ctx = ExecutionContext(
            "room-123", mock_link, AsyncMock(), on_platform_stop=observer
        )
        await ctx._begin_scope()
        ctx.observe_platform_stop(ctx.current_scope)
        await entered.wait()
        monkeypatch.setattr("band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        assert not await ctx._process_event(make_message_event(msg_id="later"))
        mock_link.mark_processing.assert_not_awaited()
        release.set()
        assert await ctx._process_event(make_message_event(msg_id="later"))
