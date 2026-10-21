"""A singleton failure cannot replay until its terminal write is confirmed."""

from __future__ import annotations

import pytest
from band_sdk_core import ParkedReason

from band.platform.event import ReconnectedEvent
from tests.platform.settlement.kit import MISSING_ATTEMPT, Reply
from tests.runtime.parking.harness import (
    OFF_FAILED,
    PAST_TWO_INTERVALS,
    RECONCILE_SECONDS,
    REFUSED,
    SPARE_RETRIES,
    Hangup,
    Harness,
    Refusal,
    RoomPeer,
)

QUEUED = OFF_FAILED.queued[0]


@pytest.mark.looptime
@pytest.mark.parametrize("scenario", [OFF_FAILED], ids=[OFF_FAILED.id], indirect=True)
class TestOffModeFailure:
    @pytest.mark.parametrize(
        ("reply", "parked"),
        [
            pytest.param(
                Refusal.ACCESS_LOST, ParkedReason.NotFound.wire_name, id="access-lost"
            ),
            pytest.param(
                MISSING_ATTEMPT, ParkedReason.Rejected.wire_name, id="rejected"
            ),
            pytest.param(REFUSED, None, id="forbidden"),
            pytest.param(Hangup(), None, id="dropped-connection"),
        ],
    )
    async def test_confirmation_releases_the_stored_continuation_without_resetting_its_budget(
        self,
        room_peer: RoomPeer,
        room: Harness,
        reply: Reply | Hangup | Refusal,
        parked: str | None,
    ) -> None:
        room_peer.script(reply, QUEUED)

        await room.join()
        await room.attempted_terminal_writes(1)
        await room.elapse(PAST_TWO_INTERVALS)

        assert room.outcome().once_each() == OFF_FAILED.after_first_attempt(
            parked=parked
        )

        room_peer.relent()
        await room.deliver(ReconnectedEvent())
        await room.elapse(SPARE_RETRIES * RECONCILE_SECONDS)
        await room.until(lambda: room.completed() == [QUEUED])
        await room.elapse(PAST_TWO_INTERVALS)

        assert (room.turns.ids, room.retained(), room_peer.completed()) == (
            [(QUEUED,)] * SPARE_RETRIES,
            {},
            {QUEUED: OFF_FAILED.terminal},
        )
