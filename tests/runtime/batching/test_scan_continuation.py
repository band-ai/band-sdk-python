"""A page of skipped inputs continues the scan at once; the end returns to idle."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room

pytestmark = pytest.mark.looptime


class TestScanContinuation:
    async def test_failed_pages_are_scanned_through_to_the_first_runnable_input(
        self, platform: FakeRoom
    ) -> None:
        platform.page_size = 2
        for index in range(5):
            platform.post(f"failed-{index}", status=DeliveryStatus.FAILED)
        platform.post("fresh")

        async with started_room(platform) as room:
            await room.quiesce()

        assert (room.turns.ids, platform.status_of("fresh")) == (
            [("fresh",)],
            DeliveryStatus.PROCESSED,
        )

    async def test_an_exhausted_scan_does_not_start_another_traversal(
        self, platform: FakeRoom
    ) -> None:
        platform.page_size = 2
        for index in range(3):
            platform.post(f"failed-{index}", status=DeliveryStatus.FAILED)

        async with started_room(platform) as room:
            await room.quiesce()

        assert platform.page_reads == 2
