"""One original delivered by REST and WebSocket runs once."""

from __future__ import annotations

from tests.runtime.batching.kit import FakeRoom, started_room


class TestDuplicateDelivery:
    async def test_a_message_from_both_sources_is_one_input(
        self, platform: FakeRoom
    ) -> None:
        event = platform.post("m1")

        async with started_room(platform, arrived=(event, event)) as room:
            await room.settled("m1")
            await room.deliver(event)
            await room.resync()

        assert room.turns.ids == [("m1",)]
