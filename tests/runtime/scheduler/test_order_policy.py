"""Selection policies share scheduling without changing singleton receive order."""

from __future__ import annotations

import pytest

from tests.runtime.delivery.kit import RestPeer, started_session


@pytest.mark.looptime
class TestOrderPolicy:
    async def test_live_order_and_request_roles(self) -> None:
        peer = RestPeer()
        async with started_session(peer, batching=False) as session:
            await session.quiesce()
            session.turns.gate = True
            await session.deliver(peer.post("warmup"))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            reads_before_arrivals = peer.polls
            await session.deliver(peer.post("z-first", at=2), peer.post("a-last", at=1))
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("z-first", "a-last")

        assert session.turns.ids == [("warmup",), ("z-first",), ("a-last",)]
        assert peer.polls == reads_before_arrivals
