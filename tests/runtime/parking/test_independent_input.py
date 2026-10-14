"""A parked intent puts no backpressure on an independently admitted input."""

from __future__ import annotations

from dataclasses import replace

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.parking.harness import Cell, Harness, Refusal, RoomPeer

OTHER = "other"


@pytest.mark.looptime
class TestIndependentInput:
    @pytest.mark.parametrize("refusal", [Refusal.DELETED, Refusal.REJECTED])
    async def test_another_input_runs_and_settles_at_once(
        self, cell: Cell, peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        peer.refuse(refusal, *cell.queued)
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        parked = cell.after_first_attempt(parked=refusal.parked)

        await room.deliver(peer.post(OTHER))
        await room.until(lambda: OTHER in peer.completed())

        assert room.outcome() == replace(
            parked,
            model=[*parked.model, (OTHER,)],
            admitted=[*parked.admitted, OTHER],
            terminal_writes=[*parked.terminal_writes, f"{OTHER}/processed"],
            completed=[OTHER],
            platform={OTHER: DeliveryStatus.PROCESSED},
        )
