"""A local non-message event ends the segment a turn may consume."""

from __future__ import annotations

from tests.conftest import make_participant_added_event
from tests.runtime.batching.kit import ROOM_ID, FakeRoom, started_room


class TestBatchBoundaries:
    async def test_a_participant_change_runs_between_the_turns_around_it(
        self, platform: FakeRoom
    ) -> None:
        async with started_room(platform, gate=True) as room:
            await room.deliver(platform.post("m1"))
            await room.until(lambda: room.turns.ids == [("m1",)])
            await room.deliver(
                platform.post("m2"),
                platform.post("m3"),
                make_participant_added_event(room_id=ROOM_ID),
                platform.post("m4"),
            )
            room.turns.release.set()
            await room.settled("m1", "m2", "m3", "m4")

        assert room.turns.ids == [
            ("m1",),
            ("m2", "m3"),
            ("participant_added",),
            ("m4",),
        ]

    async def test_a_boundary_that_arrives_during_the_page_read_still_bounds_the_turn(
        self, platform: FakeRoom
    ) -> None:
        first = platform.post("m1")
        platform.post("m2")
        platform.during_read.append(make_participant_added_event(room_id=ROOM_ID))

        async with started_room(platform, arrived=(first,)) as room:
            await room.settled("m1", "m2")

        assert room.turns.ids == [("m1",), ("participant_added",), ("m2",)]
