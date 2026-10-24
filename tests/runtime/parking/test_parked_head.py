"""A parked intent whose message /next keeps listing is not re-read in a loop."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    OFF_PROCESSED,
    PAST_TWO_INTERVALS,
    Harness,
    Refusal,
    RoomPeer,
)

# A healthy pass reads the head twice (to process it, then to see it again) and
# the room makes two passes at its start instant: startup and the resync that
# admission requests. A hot loop runs into the peer's runaway cutoff instead.
READS_PER_PASS = 2
PASSES_AT_START = 2


@pytest.mark.looptime
class TestParkedHead:
    @pytest.mark.parametrize(
        "scenario", [OFF_PROCESSED], ids=[OFF_PROCESSED.id], indirect=True
    )
    @pytest.mark.parametrize("refusal", [Refusal.REJECTED, Refusal.ACCESS_LOST])
    async def test_the_head_is_not_reread_at_one_instant(
        self, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, "m1")

        await room.join()
        await room.attempted_terminal_writes(1)
        await room.elapse(PAST_TWO_INTERVALS)

        assert room_peer.busiest_next_reads() <= READS_PER_PASS * PASSES_AT_START
