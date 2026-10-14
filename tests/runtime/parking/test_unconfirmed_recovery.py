"""A refusal that proves nothing keeps the intent retryable at the paced cadence."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import Reply
from tests.runtime.parking.harness import (
    RECONCILE_SECONDS,
    UNCONFIRMED_REPLIES,
    Cell,
    Hangup,
    Harness,
    Outcome,
    RoomPeer,
)


@pytest.mark.looptime
class TestUnconfirmedRecovery:
    @pytest.mark.parametrize(
        "reply", UNCONFIRMED_REPLIES.values(), ids=UNCONFIRMED_REPLIES.keys()
    )
    async def test_the_intent_stays_retryable_and_is_retried_once_per_interval(
        self, cell: Cell, peer: RoomPeer, room: Harness, reply: Reply | Hangup
    ) -> None:
        peer.script(reply, *cell.queued)

        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))
        await room.elapse(2 * RECONCILE_SECONDS + 1)

        assert room.outcome().once_each() == cell.after_first_attempt(parked=None)
        assert peer.attempt_offsets() == pytest.approx(
            [0, RECONCILE_SECONDS, 2 * RECONCILE_SECONDS]
        )

    @pytest.mark.parametrize(
        "reply", UNCONFIRMED_REPLIES.values(), ids=UNCONFIRMED_REPLIES.keys()
    )
    async def test_acceptance_completes_the_retained_intent_without_the_model(
        self, cell: Cell, peer: RoomPeer, room: Harness, reply: Reply | Hangup
    ) -> None:
        peer.script(reply, *cell.queued)
        await room.join()
        await room.attempted_terminal_writes(len(cell.queued))

        peer.relent()
        await room.elapse(RECONCILE_SECONDS)
        await room.until(lambda: room.retained() == {})

        assert room.outcome().once_each() == Outcome(
            model=[cell.queued],
            admitted=list(cell.queued),
            terminal_writes=cell.writes(),
            retained={},
            completed=sorted(cell.queued),
            platform=dict.fromkeys(cell.queued, cell.terminal),
        )
