"""A refusal naming the missing processing attempt parks the original intent."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.parking.harness import (
    RECONCILE_SECONDS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestRejectedIntent:
    async def test_the_intent_is_parked_as_rejected_and_never_completed_or_replayed(
        self, cell: Cell, peer: RoomPeer, room: Harness
    ) -> None:
        peer.refuse(Refusal.REJECTED, *cell.queued)

        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        await room.deliver(peer.event(cell.queued[0]))
        await room.elapse(2 * RECONCILE_SECONDS + 1)

        assert room.outcome() == cell.after_first_attempt(
            parked=Refusal.REJECTED.parked
        )
        assert peer.terminal_replies == [MISSING_ATTEMPT] * len(cell.queued)
