"""While a terminal write is unconfirmed the room keeps its ordinary work deferred."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    OTHER,
    RECONCILE_SECONDS,
    UNCONFIRMED_REPLIES,
    Cell,
    Harness,
    RoomPeer,
)

# One reply of each family: an unclassified validation error, a transient
# failure and a dropped connection.
REPRESENTATIVE_REPLIES = (
    "generic-validation-422",
    "unavailable-503",
    "dropped-connection",
)


@pytest.mark.looptime
class TestUnconfirmedBackpressure:
    @pytest.mark.parametrize("reply", REPRESENTATIVE_REPLIES)
    async def test_other_input_waits_until_the_retained_intent_is_accepted(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, reply: str
    ) -> None:
        room_peer.script(UNCONFIRMED_REPLIES[reply], *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        await room.deliver(room_peer.post(OTHER))
        await room.elapse(RECONCILE_SECONDS - 1)
        waiting = room.outcome().once_each()

        room_peer.relent()
        await room.elapse(RECONCILE_SECONDS)
        await room.settled(OTHER)

        assert waiting == scenario.after_first_attempt(parked=None)
        assert (
            room.outcome().once_each()
            == scenario.after_acceptance().with_other_settled()
        )
