"""Every selected original must be accepted before any of them runs."""

from __future__ import annotations

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room


class TestAdmission:
    async def test_a_refused_member_leaves_the_whole_selection_unexecuted(
        self, platform: FakeRoom
    ) -> None:
        platform.post("m1")
        platform.post("m2")
        platform.refuse_processing.add("m2")

        async with started_room(platform) as room:
            await room.admission_attempted("m2")

        assert (
            room.turns.ids,
            platform.status_of("m1"),
            platform.status_of("m2"),
        ) == ([], DeliveryStatus.PROCESSING, None)

    async def test_the_selection_runs_whole_once_recovery_can_admit_it(
        self, platform: FakeRoom
    ) -> None:
        platform.post("m1")
        platform.post("m2")
        platform.refuse_processing.add("m2")

        async with started_room(platform) as room:
            await room.admission_attempted("m2")
            await room.readmit()
            await room.settled("m1", "m2")

        assert room.turns.ids == [("m1", "m2")]
