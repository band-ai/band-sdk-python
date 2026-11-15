"""A room channel recovered on a live socket catches up on what it missed."""

from __future__ import annotations

from tests.scenarios.world import BandWorld


class TestRoomChannelRecovery:
    async def test_a_message_missed_while_the_channel_recovers_is_handled_promptly(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        listings = world.peer.listings
        missed = world.say_unseen("r1", "missed")

        await world.crash_room_channel("r1")
        await world.until_told("r1", f"processed {missed}")

        assert world.story("r1") == [
            f"processing {missed}",
            "turn missed",
            f"processed {missed}",
        ]
        assert world.peer.listings == listings
        assert world.server.connection_count == 1
