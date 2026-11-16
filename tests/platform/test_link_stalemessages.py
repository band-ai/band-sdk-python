"""Listing a room's messages stuck in processing, by cursor."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.message_lifecycle import ACTIONABLE_PAGE_SIZE
from tests.platform.settlement.kit import link_over
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import ROOM_ID, rest_client_over

pytestmark = pytest.mark.looptime

STUCK_ROWS = ACTIONABLE_PAGE_SIZE + 1


class TestGetStaleProcessingMessages:
    async def test_every_stuck_message_is_listed_across_cursor_pages(self) -> None:
        rest_peer = RestPeer()
        stuck = [f"stuck-{index:03}" for index in range(STUCK_ROWS)]
        rest_peer.post("failed", status=DeliveryStatus.FAILED)
        for message_id in stuck:
            rest_peer.post(message_id, status=DeliveryStatus.PROCESSING)
        rest_peer.post("fresh")

        async with rest_client_over(rest_peer.answer) as rest:
            messages = await link_over(rest).get_stale_processing_messages(ROOM_ID)

        assert (
            [message.id for message in messages],
            [read.cursor for read in rest_peer.reads],
        ) == (stuck, [None, stuck[ACTIONABLE_PAGE_SIZE - 1]])

    async def test_a_failed_listing_returns_nothing_instead_of_raising(self) -> None:
        rest_peer = RestPeer()
        rest_peer.post("stuck", status=DeliveryStatus.PROCESSING)
        rest_peer.participant = False

        async with rest_client_over(rest_peer.answer) as rest:
            messages = await link_over(rest).get_stale_processing_messages(ROOM_ID)

        assert messages == []
