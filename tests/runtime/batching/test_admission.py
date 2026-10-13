"""Every selected original must be accepted before any of them runs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room


class TestAdmission:
    @pytest.mark.asyncio
    async def test_a_refused_member_leaves_the_whole_selection_unexecuted(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.post("m1")
        platform.post("m2")
        platform.refuse_processing.add("m2")

        async with started_room(platform, mock_rest_client) as room:
            await room.until(lambda: ("processing", "m2") in platform.marks)

        assert (
            room.turns.ids,
            platform.status_of("m1"),
            platform.status_of("m2"),
        ) == ([], DeliveryStatus.PROCESSING, None)

    @pytest.mark.asyncio
    async def test_the_selection_runs_whole_once_recovery_can_admit_it(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.post("m1")
        platform.post("m2")
        platform.refuse_processing.add("m2")

        async with started_room(platform, mock_rest_client) as room:
            await room.until(lambda: ("processing", "m2") in platform.marks)
            platform.refuse_processing.clear()
            await room.ctx.request_resync()
            await room.settled("m1", "m2")

        assert room.turns.ids == [("m1", "m2")]
