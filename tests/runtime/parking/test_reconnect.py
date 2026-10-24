"""A reconnect retries a parked terminal write once, never the model."""

from __future__ import annotations

from dataclasses import replace

import pytest

from band.platform.event import ReconnectedEvent
from tests.runtime.parking.harness import (
    PARKED_REFUSALS,
    RELENTING_REFUSALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestReconnect:
    @pytest.mark.parametrize("refusal", PARKED_REFUSALS)
    async def test_a_reconnect_retries_the_terminal_write_alone_and_a_refusal_keeps_it_parked(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        await room.deliver(ReconnectedEvent())
        await room.attempted_terminal_writes(2 * len(scenario.queued))
        await room.elapse(1)

        assert room.outcome() == replace(
            scenario.after_first_attempt(parked=refusal.parked),
            terminal_writes=scenario.writes() * 2,
        )

    @pytest.mark.parametrize("refusal", RELENTING_REFUSALS)
    async def test_an_accepted_retry_completes_the_intent_without_the_model(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, refusal: Refusal
    ) -> None:
        room_peer.refuse(refusal, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        room_peer.relent()
        await room.deliver(ReconnectedEvent())
        await room.intents_resolved()

        assert room.outcome() == scenario.after_acceptance(rounds=2)
