"""Queued ordinary messages run as one turn, oldest first, up to the ceiling."""

from __future__ import annotations

from band.runtime.sessionconfig import SessionConfig
from tests.runtime.batching.kit import FakeRoom, started_room


class TestBatchTurns:
    async def test_messages_queued_behind_a_busy_turn_run_together_in_order(
        self, platform: FakeRoom
    ) -> None:
        async with started_room(platform, gate=True) as room:
            await room.deliver(platform.post("m1"))
            await room.until(lambda: room.turns.ids == [("m1",)])
            await room.deliver(
                platform.post("m2"), platform.post("m3"), platform.post("m4")
            )
            room.turns.release.set()
            await room.settled("m1", "m2", "m3", "m4")

        assert room.turns.ids == [("m1",), ("m2", "m3", "m4")]

    async def test_the_ceiling_bounds_each_turn(self, platform: FakeRoom) -> None:
        for message_id in ("m1", "m2", "m3", "m4", "m5"):
            platform.post(message_id)

        async with started_room(platform, max_queued=2) as room:
            await room.settled("m1", "m2", "m3", "m4", "m5")

        assert room.turns.ids == [("m1", "m2"), ("m3", "m4"), ("m5",)]

    async def test_a_default_config_runs_each_queued_message_as_its_own_turn(
        self, platform: FakeRoom
    ) -> None:
        async with started_room(platform, config=SessionConfig(), gate=True) as room:
            await room.deliver(platform.post("m1"))
            await room.until(lambda: room.turns.ids == [("m1",)])
            await room.deliver(platform.post("m2"), platform.post("m3"))
            room.turns.release.set()
            await room.settled("m1", "m2", "m3")

        assert room.turns.ids == [("m1",), ("m2",), ("m3",)]
