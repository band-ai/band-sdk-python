"""A rejoin retries a parked terminal write once, never the model."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.runtime.parking.harness import (
    PARKED_REFUSALS,
    RELENTING_REFUSALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestRejoin:
    @pytest.mark.parametrize("refusal", PARKED_REFUSALS)
    async def test_a_rejoin_retries_the_terminal_write_alone_and_a_refusal_keeps_it_parked(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        await room.leave()
        await room.join()
        await room.attempted_terminal_writes(2 * len(scenario.queued))
        await room.elapse(1)

        assert room.outcome() == replace(
            scenario.after_first_attempt(parked=refusal.parked),
            terminal_writes=scenario.writes() * 2,
        )

    @pytest.mark.parametrize("refusal", RELENTING_REFUSALS)
    async def test_only_an_accepted_retry_completes_the_intent(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        await room.leave()
        await room.join()
        await room.attempted_terminal_writes(2 * len(scenario.queued))
        still_refused = room.outcome()

        room_peer.relent()
        await room.leave()
        await room.join()
        await room.attempted_terminal_writes(3 * len(scenario.queued))
        await room.intents_resolved()

        assert still_refused.completed == []
        assert room.outcome() == scenario.after_acceptance(rounds=3)
