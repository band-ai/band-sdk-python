"""A socket drop while the platform holds a room's admission resync."""

from __future__ import annotations

from tests.scenarios.world import BandWorld


class TestReconnectDuringAdmission:
    async def test_a_message_missed_during_the_drop_is_recovered_once(
        self, world: BandWorld
    ) -> None:
        world.peer.hold_room("r1")
        await world.add_room("r1")
        await world.until(lambda: world.peer.held_requests != [])

        await world.drop_connection()
        missed = world.say_unseen("r1", "missed")
        world.peer.release_room("r1")
        await world.until_told("r1", f"processed {missed}")

        assert world.peer.held_requests == ["chats/r1/messages/next"]
        assert world.server.connection_count == 2
        assert world.story("r1") == [
            f"processing {missed}",
            "turn missed",
            f"processed {missed}",
        ]
