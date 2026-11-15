"""Inputs that arrive live during an outage repeat no terminal write."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    LATER_ARRIVALS,
    REFUSED,
    Cell,
    Harness,
    RoomPeer,
)


@pytest.mark.looptime
class TestLiveArrivalPacing:
    async def test_no_terminal_write_is_repeated_within_the_interval(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        arrivals = [*scenario.queued, *LATER_ARRIVALS]
        room_peer.script(REFUSED, *arrivals)
        await room.join()

        for message_id in arrivals:
            await room.deliver(room_peer.post(message_id))
            await room.elapse(1)

        assert room_peer.repeated_terminal_writes() == []
