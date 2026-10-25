"""A durably failed input is not replayed once local memory of it is gone."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, runtime_room, started_session

pytestmark = pytest.mark.looptime

FAILED_BY_THE_MODEL = "failed-by-the-model"
FOLLOWUP = "followup-after-replacement"


class TestFailedReplay:
    async def test_a_fresh_execution_does_not_replay_a_durably_failed_input(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(FAILED_BY_THE_MODEL)

        async with started_session(
            rest_peer, fail_on=frozenset({FAILED_BY_THE_MODEL}), batching=True
        ) as failed_session:
            await failed_session.reached(DeliveryStatus.FAILED, FAILED_BY_THE_MODEL)

        processing_writes = len(rest_peer.writes_of(DeliveryStatus.PROCESSING))
        async with started_session(rest_peer, batching=True) as fresh_session:
            await fresh_session.quiesce()

        assert failed_session.turns.ids == [(FAILED_BY_THE_MODEL,)]
        assert fresh_session.turns.ids == []
        assert len(rest_peer.writes_of(DeliveryStatus.PROCESSING)) == processing_writes

    async def test_a_replacement_room_does_not_replay_it(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(FAILED_BY_THE_MODEL)

        async with runtime_room(
            rest_peer, fail_on=frozenset({FAILED_BY_THE_MODEL})
        ) as room:
            await room.reached(DeliveryStatus.FAILED, FAILED_BY_THE_MODEL)
            await room.replace()
            await room.quiesce()
            await room.ctx.on_event(rest_peer.post(FOLLOWUP))
            await room.settled(FOLLOWUP)
            await room.ctx.request_resync()
            await room.quiesce()

        assert room.turns.ids == [(FAILED_BY_THE_MODEL,), (FOLLOWUP,)]
        assert room.admission_attempts == [FAILED_BY_THE_MODEL, FOLLOWUP]
