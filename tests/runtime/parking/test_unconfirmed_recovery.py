"""A refusal that proves nothing keeps the intent retryable at the paced cadence."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import Reply
from tests.runtime.parking.harness import (
    PAST_TWO_INTERVALS,
    RECONCILE_SECONDS,
    UNCONFIRMED_REPLIES,
    Cell,
    Hangup,
    Harness,
    RoomPeer,
)


@pytest.mark.looptime
class TestUnconfirmedRecovery:
    @pytest.mark.parametrize(
        "reply", UNCONFIRMED_REPLIES.values(), ids=UNCONFIRMED_REPLIES.keys()
    )
    async def test_the_intent_stays_retryable_and_is_retried_once_per_interval(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, reply: Reply | Hangup
    ) -> None:
        room_peer.script(reply, *scenario.queued)

        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))
        await room.elapse(PAST_TWO_INTERVALS)

        assert room.outcome().once_each() == scenario.after_first_attempt(parked=None)
        assert room_peer.attempt_offsets() == pytest.approx(
            [0, RECONCILE_SECONDS, 2 * RECONCILE_SECONDS]
        )

    @pytest.mark.parametrize(
        "reply", UNCONFIRMED_REPLIES.values(), ids=UNCONFIRMED_REPLIES.keys()
    )
    async def test_acceptance_completes_the_retained_intent_without_the_model(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness, reply: Reply | Hangup
    ) -> None:
        room_peer.script(reply, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        room_peer.relent()
        await room.elapse(RECONCILE_SECONDS)
        await room.intents_resolved()

        assert room.outcome().once_each() == scenario.after_acceptance()
