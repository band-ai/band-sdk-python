"""A rejoin retries a parked terminal write once, never the model."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.runtime.parking.harness import (
    RELENTING_REFUSALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestRejoin:
    @pytest.mark.parametrize("refusal", list(Refusal))
    async def test_a_rejoin_retries_the_terminal_write_alone_and_a_refusal_keeps_it_parked(
        self, cell: Cell, peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        peer.refuse(refusal, *cell.queued)
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))

        await room.leave()
        await room.join()
        await room.attempted_terminal_writes(2 * len(cell.queued))
        await room.elapse(1)

        assert room.outcome() == replace(
            cell.after_first_attempt(parked=refusal.parked),
            terminal_writes=cell.writes() * 2,
        )

    @pytest.mark.parametrize("refusal", RELENTING_REFUSALS)
    async def test_only_an_accepted_retry_completes_the_intent(
        self, cell: Cell, peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        peer.refuse(refusal, *cell.queued)
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        await room.leave()
        await room.join()
        await room.attempted_terminal_writes(2 * len(cell.queued))
        still_refused = room.outcome()

        peer.relent()
        await room.leave()
        await room.join()
        await room.attempted_terminal_writes(3 * len(cell.queued))
        await room.intents_resolved()

        assert still_refused.completed == []
        assert room.outcome() == cell.after_acceptance(rounds=3)
