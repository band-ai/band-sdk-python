"""Control signals arriving inside the claim window."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import auxiliary_context, backlog_message
from tests.runtime.helpers import LifecyclePlatform, ResponseGate, rest_client_over
from tests.runtime.scheduler.driver import attempt_delivery


class TestControlSignalInClaimWindow:
    """Signals landing while processing acceptance is still pending.

    The scope is admitted only after every acceptance is confirmed, so a signal
    in this window consumes nothing and leaves the message recoverable.
    """

    @staticmethod
    @contextlib.asynccontextmanager
    async def _claim_window(
        message_id: str,
    ) -> AsyncIterator[
        tuple[LifecyclePlatform, ResponseGate, ExecutionContext, BlockingHandler]
    ]:
        """A room whose processing claim parks until the test releases it."""
        peer = LifecyclePlatform()
        peer.add_message(message_id)
        gate = ResponseGate(peer, "/processing")
        handler = BlockingHandler()
        async with rest_client_over(gate.answer) as rest:
            yield peer, gate, auxiliary_context(rest, handler), handler

    @staticmethod
    def _actionable(peer: LifecyclePlatform) -> list[str]:
        return [message["id"] for message in peer.messages]

    async def test_interrupt_in_window_consumes_nothing(self):
        async with self._claim_window("w1") as (peer, gate, ctx, handler):
            proc = asyncio.create_task(attempt_delivery(ctx, peer.event("w1")))
            await gate.entered.wait()
            # Not admitted yet: nothing to terminate.
            assert ctx.interrupt() is False
            gate.release.set()
            result = await proc

        assert result is TurnOutcome.RETRY_LATER
        assert handler.invoked == []
        assert peer.requested_marks == [("w1", "processing")]
        assert self._actionable(peer) == ["w1"]

    async def test_stop_in_window_leaves_message_actionable(self):
        async with self._claim_window("w2") as (peer, gate, ctx, handler):
            proc = asyncio.create_task(attempt_delivery(ctx, peer.event("w2")))
            await gate.entered.wait()
            ctx.stop_room()
            gate.release.set()
            result = await proc

        assert result is TurnOutcome.PAUSED
        assert handler.invoked == []
        assert ctx.is_stopped
        assert peer.requested_marks == [("w2", "processing")]
        assert self._actionable(peer) == ["w2"]

    async def test_interrupt_in_backlog_window_defers_without_consuming(self):
        async with self._claim_window("bw1") as (peer, gate, ctx, handler):
            proc = asyncio.create_task(attempt_delivery(ctx, backlog_message("bw1")))
            await gate.entered.wait()
            assert ctx.interrupt() is False
            gate.release.set()
            result = await proc

        assert result is TurnOutcome.RETRY_LATER
        assert handler.invoked == []
        assert peer.requested_marks == [("bw1", "processing")]
        assert self._actionable(peer) == ["bw1"]

    async def test_interrupt_between_cycles_arms_nothing(self, mock_link):
        """A truly idle interrupt (no claim in flight) stays a no-op and does not
        arm a pending signal that would mis-flag the next message."""
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        assert ctx.interrupt() is False

        # The next message runs to completion, unaffected.
        result = await attempt_delivery(ctx, make_message_event(msg_id="n1"))
        assert result is TurnOutcome.ADVANCED
        assert handler.completed == ["n1"]
