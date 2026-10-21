"""Selection policies share scheduling without changing singleton arrival order."""

from __future__ import annotations

import pytest

from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestOrderPolicy:
    async def test_live_order_and_request_roles(self) -> None:
        peer = RestPeer()
        async with started_session(peer, batching=False) as session:
            await session.quiesce()
            session.turns.gate = True
            await session.deliver(peer.post("warmup"))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            reads_before_arrivals = peer.polls
            await session.deliver(peer.post("first", at=1), peer.post("second", at=2))
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("first", "second")

        assert session.turns.ids == [("warmup",), ("first",), ("second",)]
        assert peer.polls == reads_before_arrivals
