"""The latest STOP or INTERRUPT before a turn settles decides its disposition."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestControlPrecedence:
    async def test_an_interrupt_after_a_stop_consumes_the_message(
        self, rest_peer: RestPeer
    ) -> None:
        first = rest_peer.post("m1")
        async with started_session(rest_peer, gate=True) as session:
            await session.deliver(first)
            await session.until(lambda: session.turns.ids == [("m1",)])
            session.ctx.stop_room()
            assert session.ctx.interrupt()
            await session.settled("m1")
            await session.ctx.resume_room()
            await session.quiesce()

        assert session.turns.ids == [("m1",)]

    async def test_a_stop_after_an_interrupt_leaves_the_message_to_replay(
        self, rest_peer: RestPeer
    ) -> None:
        first = rest_peer.post("m1")
        async with started_session(rest_peer, gate=True) as session:
            await session.deliver(first)
            await session.until(lambda: session.turns.ids == [("m1",)])
            assert session.ctx.interrupt()
            session.ctx.stop_room()
            await session.quiesce()
            held = rest_peer.status_of("m1")

            session.turns.release.set()
            await session.ctx.resume_room()
            await session.settled("m1")

        assert held is DeliveryStatus.PROCESSING
        assert session.turns.ids == [("m1",), ("m1",)]
