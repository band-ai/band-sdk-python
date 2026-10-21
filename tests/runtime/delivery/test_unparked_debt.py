"""A write unparked while a turn runs holds the next ordinary input behind it."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ReconnectedEvent
from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.delivery.kit import RestPeer, started_session, turn, write

pytestmark = pytest.mark.looptime


class TestUnparkedDebt:
    async def test_the_next_input_waits_for_a_write_unparked_mid_turn(
        self, rest_peer: RestPeer
    ) -> None:
        first = rest_peer.post("m1")
        async with started_session(rest_peer, gate=True) as session:
            rest_peer.terminal_error = MISSING_ATTEMPT
            await session.deliver(first)
            session.turns.release.set()
            await session.until(lambda: rest_peer.writes_of(DeliveryStatus.PROCESSED))
            await session.quiesce()
            rest_peer.terminal_error = None
            session.turns.release.clear()

            rest_peer.post("m2")
            rest_peer.post("m3")
            await session.ctx.request_resync()
            await session.until(lambda: ("m2",) in session.turns.ids)
            await session.deliver(ReconnectedEvent())
            session.turns.release.set()
            await session.settled("m1", "m2", "m3")

        retried, third = write(DeliveryStatus.PROCESSED, "m1"), turn("m3")
        journal = rest_peer.without_reads()
        assert journal.index(retried) < journal.index(third)
