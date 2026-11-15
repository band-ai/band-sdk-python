"""After a 404, another input runs only once its own admission is accepted."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    OTHER,
    PAST_TWO_INTERVALS,
    RECONCILE_SECONDS,
    REFUSED,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestOwnAdmission:
    async def test_an_input_runs_only_once_its_own_admission_is_accepted(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        room_peer.refuse(Refusal.DELETED, *scenario.queued)
        room_peer.admission[OTHER] = REFUSED
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        gone = scenario.after_gone()

        await room.deliver(room_peer.post(OTHER))
        await room.admission_attempted(OTHER)
        refused = room.outcome()

        room_peer.admission.clear()
        await room.elapse(RECONCILE_SECONDS)
        await room.settled(OTHER)

        assert refused == gone
        assert room.outcome() == gone.with_other_settled()

    async def test_lost_access_refuses_every_admission_and_nothing_runs(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        room_peer.refuse(Refusal.ACCESS_LOST, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        parked = scenario.after_first_attempt(parked=Refusal.ACCESS_LOST.parked)

        await room.deliver(room_peer.post(OTHER))
        await room.elapse(PAST_TWO_INTERVALS)

        assert room.outcome() == parked
