"""A scan left open by a blocked input must not hide an earlier reopened row."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import (
    EARLIER_ROW,
    FAILED_ROWS,
    RestPeer,
    started_session,
)

pytestmark = pytest.mark.looptime


class TestBlockedTraversal:
    async def test_a_row_reopened_behind_a_blocked_input_is_found_by_the_next_scan(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")
        rest_peer.refuse_processing.add("new")

        async with started_session(rest_peer, batching=True) as session:
            await session.quiesce()
            # The scan reached "new" and stopped there; another runtime then
            # reopens a row that lies before it.
            rest_peer.set_status(EARLIER_ROW, DeliveryStatus.PROCESSING)
            await session.resync()

        assert session.turns.ids == [(EARLIER_ROW,)]
