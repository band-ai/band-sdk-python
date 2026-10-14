"""An agent-scoped interrupt across rooms with different executions."""

from __future__ import annotations

from band.client.streaming import ControlMode
from tests.scenarios.world import BandWorld


class TestAgentWideInterrupt:
    async def test_every_room_is_interrupted_and_a_bare_execution_degrades(
        self, world_with_bare_room: BandWorld
    ) -> None:
        world = world_with_bare_room
        for room_id in ("r1", "r2", "bare"):
            await world.join(room_id)
        world.script.hold_turn("a")
        world.script.hold_turn("b")
        a = await world.say("r1", "a")
        b = await world.say("r2", "b")
        await world.until_told("r1", "turn a")
        await world.until_told("r2", "turn b")

        await world.control(ControlMode.INTERRUPT, correlation_id="interrupt-all")
        await world.until_told("bare", "control interrupt")
        await world.until_told("r1", f"processed {a}")
        await world.until_told("r2", f"processed {b}")
        after = await world.say("r1", "after")
        await world.say("bare", "still served")
        await world.until_told("r1", f"processed {after}")
        await world.until_told("bare", "bare saw still served")

        assert world.story("r1") == [
            f"processing {a}",
            "turn a",
            "control interrupt",
            "turn a cancelled",
            f"processed {a}",
            f"processing {after}",
            "turn after",
            f"processed {after}",
        ]
        assert world.story("bare") == [
            "bare start",
            "control interrupt",
            "bare saw still served",
        ]
