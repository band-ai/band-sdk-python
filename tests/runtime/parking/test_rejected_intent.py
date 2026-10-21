"""A refusal naming the missing processing attempt parks the original intent."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.parking.harness import (
    PAST_TWO_INTERVALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestRejectedIntent:
    async def test_the_intent_is_parked_as_rejected_and_never_completed_or_replayed(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        room_peer.refuse(Refusal.REJECTED, *scenario.queued)

        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        await room.deliver(room_peer.event(scenario.queued[0]))
        await room.elapse(PAST_TWO_INTERVALS)

        assert room.outcome() == scenario.after_first_attempt(
            parked=Refusal.REJECTED.parked
        )
        assert room_peer.terminal_replies == [MISSING_ATTEMPT] * len(scenario.queued)
