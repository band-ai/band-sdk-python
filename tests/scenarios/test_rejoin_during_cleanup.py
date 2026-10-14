"""Leaving a room mid-turn and rejoining before its cleanup ends."""

from __future__ import annotations

from tests.scenarios.world import BandWorld


class TestRejoinDuringCleanup:
    async def test_the_rejoined_room_recovers_its_queue_exactly_once(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        world.script.hold_turn("first")
        first = await world.say("r1", "first")
        await world.until_told("r1", "turn first")

        world.script.hold_cleanups()
        await world.remove_room("r1")
        await world.until_told("r1", "cleanup started")
        world.script.release_turn("first")
        second = world.say_unseen("r1", "second")
        await world.add_room("r1")

        world.script.release_cleanups()
        await world.until_told("r1", f"processed {second}")

        assert world.story("r1") == [
            f"processing {first}",
            "turn first",
            "turn first cancelled",
            "cleanup started",
            "cleanup done",
            f"processing {first}",
            "turn first",
            f"processed {first}",
            f"processing {second}",
            "turn second",
            f"processed {second}",
        ]
        assert world.runtime.unsettled_rooms == []
