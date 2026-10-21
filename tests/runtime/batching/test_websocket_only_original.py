"""A delivery the REST list omits still runs, ordered by its own position."""

from __future__ import annotations

from tests.runtime.batching.kit import FakeRoom, started_room


class TestWebSocketOnlyOriginal:
    async def test_it_runs_alone_between_the_rest_work_around_it(
        self, platform: FakeRoom
    ) -> None:
        platform.post("m1", at=1)
        platform.post("m3", at=3)
        reply = platform.post("reply", at=2, in_rest=False)

        async with started_room(platform, arrived=(reply,)) as room:
            await room.settled("m1", "reply", "m3")

        assert room.turns.ids == [("m1",), ("reply",), ("m3",)]

    async def test_a_refused_admission_keeps_its_payload_for_the_next_pass(
        self, platform: FakeRoom
    ) -> None:
        platform.refuse_processing.add("reply")

        async with started_room(platform) as room:
            await room.deliver(platform.post("reply", in_rest=False))
            await room.admission_attempted("reply")
            await room.readmit()
            await room.settled("reply")

        assert room.turns.ids == [("reply",)]
