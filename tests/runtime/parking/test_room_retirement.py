"""A membership-loss event retires the room; the unresolved intent is not a success."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    PARKED_REFUSALS,
    PAST_TWO_INTERVALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestRoomRetirement:
    @pytest.mark.parametrize("refusal", PARKED_REFUSALS)
    async def test_observed_removal_retires_the_room_without_settling_the_intent(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        await room.leave()
        await room.elapse(PAST_TWO_INTERVALS)

        assert (room.serving, room.outcome()) == (
            False,
            scenario.after_first_attempt(parked=refusal.parked),
        )
