"""After a 404, another input runs only once its own admission is accepted."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    OTHER,
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
        self, cell: Cell, peer: RoomPeer, room: Harness
    ) -> None:
        peer.refuse(Refusal.DELETED, *cell.queued)
        peer.admission[OTHER] = REFUSED
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        parked = cell.after_first_attempt(parked=Refusal.DELETED.parked)

        await room.deliver(peer.post(OTHER))
        await room.admission_attempted(OTHER)
        refused = room.outcome()

        peer.admission.clear()
        await room.elapse(RECONCILE_SECONDS)
        await room.settled(OTHER)

        assert refused == parked
        assert room.outcome() == parked.with_other_settled()

    async def test_lost_access_refuses_every_admission_and_nothing_runs(
        self, cell: Cell, peer: RoomPeer, room: Harness
    ) -> None:
        peer.refuse(Refusal.ACCESS_LOST, *cell.queued)
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        parked = cell.after_first_attempt(parked=Refusal.ACCESS_LOST.parked)

        await room.deliver(peer.post(OTHER))
        await room.admission_attempted(OTHER)
        await room.elapse(2 * RECONCILE_SECONDS + 1)

        assert room.outcome() == parked
