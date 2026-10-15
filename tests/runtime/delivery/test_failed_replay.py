"""A durably failed input is not replayed once local memory of it is gone."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, runtime_room, started_session
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime

FAILED_BY_THE_MODEL = "failed-by-the-model"


class TestFailedReplay:
    async def test_eviction_from_the_completed_memory_does_not_replay_it(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(FAILED_BY_THE_MODEL)

        async with started_session(
            rest_peer, fail_on=frozenset({FAILED_BY_THE_MODEL})
        ) as session:
            await session.reached(DeliveryStatus.FAILED, FAILED_BY_THE_MODEL)
            claims = session.ctx.claims
            for index in range(claims.max_completed):
                claims.remember_completed(ROOM_ID, f"completed-{index}")
            forgotten = not claims.is_completed(ROOM_ID, FAILED_BY_THE_MODEL)
            await session.resync()

        assert (forgotten, session.turns.ids, session.admission_attempts) == (
            True,
            [(FAILED_BY_THE_MODEL,)],
            [FAILED_BY_THE_MODEL],
        )

    async def test_a_replacement_room_does_not_replay_it(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(FAILED_BY_THE_MODEL)

        async with runtime_room(
            rest_peer, fail_on=frozenset({FAILED_BY_THE_MODEL})
        ) as room:
            await room.reached(DeliveryStatus.FAILED, FAILED_BY_THE_MODEL)
            original = room.ctx
            await room.replace()
            replaced = room.ctx is not original

        assert (replaced, room.turns.ids, room.admission_attempts) == (
            True,
            [(FAILED_BY_THE_MODEL,)],
            [FAILED_BY_THE_MODEL],
        )
