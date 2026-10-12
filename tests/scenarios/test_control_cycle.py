"""STOP, duplicate STOP, a stray INTERRUPT and PLAY across one room's turns."""

from __future__ import annotations

from band.client.streaming import ControlMode
from tests.scenarios.world import BandWorld


class TestControlCycle:
    async def test_stop_pauses_claims_and_play_drains_the_backlog_in_order(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        world.script.hold_turn("long")
        long = await world.say("r1", "long")
        await world.until_told("r1", "turn long")

        await world.control(ControlMode.STOP, room_id="r1", correlation_id="stop-1")
        await world.until_told("r1", "turn long cancelled")
        during = await world.say("r1", "during stop")
        await world.control(ControlMode.STOP, room_id="r1", correlation_id="stop-1")
        await world.control(
            ControlMode.INTERRUPT, room_id="elsewhere", correlation_id="interrupt-1"
        )
        world.script.release_turn("long")

        await world.control(ControlMode.PLAY, room_id="r1", correlation_id="play-1")
        await world.until_told("r1", f"processed {during}")

        assert world.story("r1") == [
            f"processing {long}",
            "turn long",
            "control stop",
            "turn long cancelled",
            f"processing {long}",
            "turn long",
            f"processed {long}",
            f"processing {during}",
            "turn during stop",
            f"processed {during}",
        ]
        assert world.story("elsewhere") == []
