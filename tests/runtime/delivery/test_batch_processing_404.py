"""A batch with one refused processing write must not run a partial turn."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime

PROCESSING_404_ID = "m2"


class TestBatchProcessing404:
    async def test_a_processing_404_leaves_the_whole_batch_for_a_later_admission(
        self, rest_peer: RestPeer
    ) -> None:
        message_ids = ("m1", PROCESSING_404_ID, "m3")
        for message_id in message_ids:
            rest_peer.post(message_id)
        rest_peer.processing_not_found_once.add(PROCESSING_404_ID)

        async with started_session(rest_peer, batching=True) as session:
            await session.quiesce()

            assert session.progress == ([], ["m1", "m2"])

            await session.resync()
            await session.settled(*message_ids)

        assert session.progress == (
            [message_ids],
            [*message_ids[:2], *message_ids],
        )
        assert all(
            rest_peer.status_of(message_id) is DeliveryStatus.PROCESSED
            for message_id in message_ids
        )
