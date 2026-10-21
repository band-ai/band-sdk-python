"""A turn's terminal write is confirmed before the next input runs, however it ended."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.platform.settlement.kit import Reply
from tests.runtime.delivery.kit import Ending, RestPeer, started_session

pytestmark = pytest.mark.looptime

UNAVAILABLE = Reply(503, "{}")
ENDINGS = pytest.mark.parametrize(
    "ending", list(Ending), ids=lambda ending: ending.value
)


class TestOwedWrite:
    @ENDINGS
    async def test_the_next_input_waits_for_an_unconfirmed_terminal_write(
        self, rest_peer: RestPeer, ending: Ending
    ) -> None:
        first, second = rest_peer.post("m1"), rest_peer.post("m2")
        async with started_session(
            rest_peer, gate=True, fail_on=ending.fail_on
        ) as session:
            await session.deliver(first)
            await session.until(lambda: session.turns.ids == [("m1",)])
            await session.deliver(second)
            rest_peer.terminal_error = UNAVAILABLE
            ending.end(session)
            await session.quiesce()
            held = session.progress, rest_peer.status_of("m1")

            rest_peer.terminal_error = None
            session.turns.release.set()
            await session.resync()
            await session.reached(ending.terminal, "m1")
            await session.settled("m2")

        assert held == (([("m1",)], ["m1"]), DeliveryStatus.PROCESSING)
        assert session.turns.ids == [("m1",), ("m2",)]

    @ENDINGS
    async def test_a_confirmed_terminal_write_lets_the_next_input_run(
        self, rest_peer: RestPeer, ending: Ending
    ) -> None:
        first, second = rest_peer.post("m1"), rest_peer.post("m2")
        async with started_session(
            rest_peer, gate=True, fail_on=ending.fail_on
        ) as session:
            await session.deliver(first)
            await session.until(lambda: session.turns.ids == [("m1",)])
            await session.deliver(second)
            ending.end(session)
            session.turns.release.set()
            await session.reached(ending.terminal, "m1")
            await session.settled("m2")

        assert session.turns.ids == [("m1",), ("m2",)]
