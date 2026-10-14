"""A 404 after model work leaves an unresolved intent, whatever its cause."""

from __future__ import annotations

import pytest

from tests.runtime.parking.harness import (
    NOT_FOUND,
    NOT_FOUND_REFUSALS,
    RECONCILE_SECONDS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestUnknownCause:
    @pytest.mark.parametrize("refusal", NOT_FOUND_REFUSALS)
    async def test_the_intent_is_retained_unresolved_and_never_executed(
        self, cell: Cell, peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        peer.refuse(refusal, *cell.queued)

        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        await room.deliver(peer.event(cell.queued[0]))
        await room.elapse(2 * RECONCILE_SECONDS + 1)

        assert room.outcome() == cell.after_first_attempt(parked=refusal.parked)
        assert peer.terminal_replies == [NOT_FOUND] * len(cell.queued)
