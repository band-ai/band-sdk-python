"""A 404 while the room is unreadable leaves an unresolved intent, never executed."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    NOT_FOUND,
    PAST_TWO_INTERVALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestUnknownCause:
    async def test_the_intent_is_retained_unresolved_and_never_executed(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        refusal = Refusal.ACCESS_LOST
        room_peer.refuse(refusal, *scenario.queued)

        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        await room.deliver(room_peer.event(scenario.queued[0]))
        await room.elapse(PAST_TWO_INTERVALS)

        assert room.outcome() == scenario.after_first_attempt(parked=refusal.parked)
        assert room_peer.terminal_replies == [NOT_FOUND] * len(scenario.queued)
