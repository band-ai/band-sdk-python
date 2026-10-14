"""A parked intent puts no backpressure on an independently admitted input."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import OTHER, Cell, Harness, Refusal, RoomPeer


@pytest.mark.looptime
class TestIndependentInput:
    @pytest.mark.parametrize("refusal", [Refusal.DELETED, Refusal.REJECTED])
    async def test_another_input_runs_and_settles_at_once(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        parked = scenario.after_first_attempt(parked=refusal.parked)

        await room.deliver(room_peer.post(OTHER))
        await room.settled(OTHER)

        assert room.outcome() == parked.with_other_settled()
