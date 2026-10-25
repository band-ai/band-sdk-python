"""Batching keeps the chronological ordinary prefix in one turn."""

from __future__ import annotations

import pytest

from band.runtime.execution import ExecutionState
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestBatchOrderPolicy:
    async def test_live_arrivals_share_the_next_turn(self) -> None:
        peer = RestPeer()
        async with started_session(peer, batching=True) as session:
            await session.until(lambda: session.ctx.state is ExecutionState.IDLE)
            session.turns.gate = True
            await session.deliver(peer.post("warmup"))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            await session.deliver(peer.post("first", at=1), peer.post("second", at=2))
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("first", "second")

        assert session.turns.ids == [("warmup",), ("first", "second")]
