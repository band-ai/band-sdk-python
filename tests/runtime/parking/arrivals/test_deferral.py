"""An input that arrives live while a terminal write is unconfirmed waits behind it."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    LATER_ARRIVALS,
    RECONCILE_SECONDS,
    REFUSED,
    Cell,
    Harness,
    Outcome,
    RoomPeer,
)


@pytest.mark.looptime
class TestLiveArrivalDeferral:
    async def test_later_arrivals_run_once_the_write_is_accepted(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        first = scenario.queued[0]
        room_peer.script(REFUSED, first)
        await room.join()
        await room.deliver(room_peer.post(first))
        await room.attempted_terminal_writes(1)

        for message_id in LATER_ARRIVALS:
            await room.deliver(room_peer.post(message_id))
        await room.elapse(RECONCILE_SECONDS - 1)
        waiting = room.outcome().once_each()

        room_peer.relent()
        await room.elapse(RECONCILE_SECONDS)
        await room.settled(LATER_ARRIVALS[-1])

        assert waiting == Outcome(
            model=[(first,)],
            admitted=[first],
            terminal_writes=[f"{first}/{scenario.terminal}"],
            retained={first: (scenario.terminal, None)},
            completed=[],
            platform={},
        )
        assert room.outcome().retained == {}
        assert room.outcome().completed == sorted([first, *LATER_ARRIVALS])
