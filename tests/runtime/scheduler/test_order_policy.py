"""Live WebSocket arrivals run in arrival order and add no REST reads."""

from __future__ import annotations

import pytest

from band.runtime.execution import ExecutionState
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestOrderPolicy:
    @pytest.mark.parametrize(
        ("first_at", "second_at"),
        [(1, 2), (2, 1), (1, 1)],
        ids=["chronological", "older-arrives-later", "same-time-reversed-ids"],
    )
    async def test_live_arrivals_run_in_order_without_rest_reads(
        self, first_at: int, second_at: int
    ) -> None:
        peer = RestPeer()
        async with started_session(peer, batching=False) as session:
            await session.until(lambda: session.ctx.state is ExecutionState.IDLE)
            session.turns.gate = True
            await session.deliver(peer.post("warmup"))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            rest_reads_before_arrivals = peer.polls
            await session.deliver(
                peer.post("z-first", at=first_at), peer.post("a-second", at=second_at)
            )
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("z-first", "a-second")

        assert session.turns.ids == [("warmup",), ("z-first",), ("a-second",)]
        assert peer.polls == rest_reads_before_arrivals
