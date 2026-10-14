"""After a 404, another input runs only once its own admission is accepted."""

from __future__ import annotations

from dataclasses import replace

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.parking.harness import (
    RECONCILE_SECONDS,
    REFUSED,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)

OTHER = "other"


@pytest.mark.looptime
class TestOwnAdmission:
    async def test_an_input_runs_only_once_its_own_admission_is_accepted(
        self, cell: Cell, peer: RoomPeer, room: Harness
    ) -> None:
        peer.refuse(Refusal.DELETED, *cell.queued)
        peer.admission[OTHER] = REFUSED
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        parked = cell.after_first_attempt(parked="not_found")

        await room.deliver(peer.post(OTHER))
        await room.until(lambda: f"{OTHER}/processing" in peer.writes)
        refused = room.outcome()

        peer.admission.clear()
        await room.elapse(RECONCILE_SECONDS)
        await room.until(lambda: OTHER in peer.completed())

        assert refused == parked
        assert room.outcome() == replace(
            parked,
            model=[*parked.model, (OTHER,)],
            admitted=[*parked.admitted, OTHER],
            terminal_writes=[*parked.terminal_writes, f"{OTHER}/processed"],
            completed=[OTHER],
            platform={OTHER: DeliveryStatus.PROCESSED},
        )

    async def test_lost_access_refuses_every_admission_and_nothing_runs(
        self, cell: Cell, peer: RoomPeer, room: Harness
    ) -> None:
        peer.refuse(Refusal.ACCESS_LOST, *cell.queued)
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        parked = cell.after_first_attempt(parked="not_found")

        await room.deliver(peer.post(OTHER))
        await room.until(lambda: f"{OTHER}/processing" in peer.writes)
        await room.elapse(2 * RECONCILE_SECONDS + 1)

        assert room.outcome() == parked
