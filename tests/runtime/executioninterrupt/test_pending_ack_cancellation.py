"""Cancellation while an acknowledgement is pending."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Coroutine
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext, TurnOutcome
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)


class TestPendingAckCancellationGap:
    """Regression coverage for the in-process pending-ACK cancellation gap:
    once the handler has run to completion, the message must be marked
    ack-pending BEFORE the awaited mark_processed call, so a
    genuine cancellation of the enclosing task landing inside that await
    still routes redelivery through the ack-retry path instead of replaying
    the handler. Scoped to in-process cancellation with the same live
    ClaimRegistry still reachable -- not a process-restart durability
    guarantee (see execution.py's ``_abort_cycle``/step-4 docs).
    """

    @staticmethod
    def _gated_mark_processed(
        entered: asyncio.Event, release: asyncio.Event
    ) -> AsyncMock:
        """mark_processed that parks so a test can cancel the caller while
        this exact await is in flight."""

        async def _gate(room_id: str, msg_id: str) -> bool:
            entered.set()
            await release.wait()
            return True

        return AsyncMock(side_effect=_gate)

    @staticmethod
    @contextlib.asynccontextmanager
    async def _running(coro: Coroutine[Any, Any, Any]) -> AsyncIterator[asyncio.Task]:
        """Run `coro` as a task, guaranteeing it's cancelled and drained on
        exit. `_gate` above parks on `release`, which nothing sets on this
        path -- an assertion failing before the test's own explicit cancel
        would otherwise leak a task that can never finish on its own.
        """
        task = asyncio.create_task(coro)
        try:
            yield task
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_websocket_path_cancellation_during_mark_processed(self, mock_link):
        entered, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processed = self._gated_mark_processed(entered, release)
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        async with self._running(
            ctx._process_event(make_message_event(msg_id="ws-cancel-ack"))
        ) as proc:
            await entered.wait()

            # The handler already ran to completion; remember_ack_pending runs
            # synchronously before this awaited mark_processed call.
            assert handler.completed == ["ws-cancel-ack"]
            assert ctx.claims.is_ack_pending("room-123", "ws-cancel-ack")
            assert not ctx.claims.is_completed("room-123", "ws-cancel-ack")

            proc.cancel()
            with pytest.raises(asyncio.CancelledError):
                await proc

        # Cancellation must not have undone the ack-pending marker -- never
        # completed, never neither.
        assert ctx.claims.is_ack_pending("room-123", "ws-cancel-ack")
        assert not ctx.claims.is_completed("room-123", "ws-cancel-ack")

        # A subsequent delivery against the same live registry retries only
        # the ack -- the handler is never re-invoked.
        mock_link.mark_processed = AsyncMock(return_value=True)
        result = await ctx._process_event(make_message_event(msg_id="ws-cancel-ack"))

        assert result is TurnOutcome.ADVANCED
        assert handler.invocations == 1
        assert ctx.claims.is_completed("room-123", "ws-cancel-ack")
        assert not ctx.claims.is_ack_pending("room-123", "ws-cancel-ack")

    async def test_backlog_path_cancellation_during_mark_processed(self, mock_link):
        entered, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processed = self._gated_mark_processed(entered, release)
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        msg = backlog_message("bk-cancel-ack")

        async with self._running(ctx._process_backlog_message(msg)) as proc:
            await entered.wait()

            assert handler.completed == ["bk-cancel-ack"]
            assert ctx.claims.is_ack_pending("room-123", "bk-cancel-ack")
            assert not ctx.claims.is_completed("room-123", "bk-cancel-ack")

            proc.cancel()
            with pytest.raises(asyncio.CancelledError):
                await proc

        assert ctx.claims.is_ack_pending("room-123", "bk-cancel-ack")
        assert not ctx.claims.is_completed("room-123", "bk-cancel-ack")

        mock_link.mark_processed = AsyncMock(return_value=True)
        result = await ctx._process_backlog_message(msg)

        assert result is TurnOutcome.ADVANCED
        assert handler.invocations == 1
        assert ctx.claims.is_completed("room-123", "bk-cancel-ack")
        assert not ctx.claims.is_ack_pending("room-123", "bk-cancel-ack")


# Short enough to fire immediately against a handler that blocks; not tied to
# any real-world budget, just "small" for these deterministic tests.
