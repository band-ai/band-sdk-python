"""Channels recovered on a live socket catch up on what they missed."""

from __future__ import annotations

from tests.scenarios.world import BandWorld


class TestRoomMembershipRecovery:
    async def test_a_room_added_while_the_channel_recovers_is_served_promptly(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        world.add_room_unseen("r2")

        await world.crash_rooms_channel()
        await world.until(lambda: world.serving("r2"))

        assert world.serving("r1")
        assert world.server.connection_count == 1

    async def test_both_channels_recovering_together_catch_up_what_each_missed(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        missed = world.say_unseen("r1", "missed")
        world.add_room_unseen("r2")

        await world.crash_room_channel("r1")
        await world.crash_rooms_channel()
        await world.until_told("r1", f"processed {missed}")
        await world.until(lambda: world.serving("r2"))

        assert world.serving("r1")
        assert world.server.connection_count == 1
