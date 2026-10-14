"""An off-mode failed turn keeps no terminal intent; its retry policy decides replays."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.platform.settlement.kit import MISSING_ATTEMPT, Reply
from tests.runtime.parking.harness import (
    NOT_FOUND,
    RECONCILE_SECONDS,
    REFUSED,
    SPARE_RETRIES,
    Cell,
    Hangup,
    Harness,
    Mode,
    RoomPeer,
)

QUEUED = "m1"


@pytest.fixture
def cell() -> Cell:
    return Cell(Mode.OFF, DeliveryStatus.FAILED)


@pytest.mark.looptime
class TestOffModeFailure:
    @pytest.mark.parametrize(
        "reply",
        [NOT_FOUND, MISSING_ATTEMPT, REFUSED, Hangup()],
        ids=["not-found", "rejected", "forbidden", "dropped-connection"],
    )
    async def test_no_intent_is_retained_and_the_retry_budget_alone_bounds_replays(
        self, peer: RoomPeer, room: Harness, reply: Reply | Hangup
    ) -> None:
        peer.script(reply, QUEUED)

        await room.join()
        await room.until(lambda: len(room.turns.ids) == SPARE_RETRIES)
        await room.elapse(2 * RECONCILE_SECONDS + 1)

        assert (room.turns.ids, room.retained()) == (
            [(QUEUED,)] * SPARE_RETRIES,
            {},
        )
