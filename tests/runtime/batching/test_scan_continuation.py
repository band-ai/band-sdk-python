"""A page of skipped inputs continues the scan at once; the end returns to idle."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room


class TestScanContinuation:
    @pytest.mark.asyncio
    async def test_failed_pages_are_scanned_through_to_the_first_runnable_input(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.page_size = 2
        for index in range(5):
            platform.post(f"failed-{index}", status=DeliveryStatus.FAILED)
        platform.post("fresh")

        async with started_room(platform, mock_rest_client) as room:
            await room.settled("fresh")

        assert room.turns.ids == [("fresh",)]
        assert room.ctx._scan_cursor is None

    @pytest.mark.asyncio
    async def test_an_exhausted_scan_does_not_start_another_traversal(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.page_size = 2
        for index in range(3):
            platform.post(f"failed-{index}", status=DeliveryStatus.FAILED)

        async with started_room(platform, mock_rest_client) as room:
            await room.until(lambda: room.ctx.link.get_actionable_page.await_count >= 2)
            reads = room.ctx.link.get_actionable_page.await_count
            await asyncio.sleep(0.1)

        assert (reads, room.ctx.link.get_actionable_page.await_count) == (2, 2)
