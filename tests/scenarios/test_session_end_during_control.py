"""The session ends while a control signal is still being applied: a
supersede or the agent's own stop must not wait for it."""

from __future__ import annotations

from websockets.frames import CloseCode

from band.client.streaming import ControlMode
from tests.conftest import TERMINAL_SUPERSEDE
from tests.scenarios.world import SESSION_SUBJECT, BandWorld

# A terminal supersede ends ``run_forever`` by raising its typed reason.
ENDED_BY_SUPERSEDE = f"ended by {TERMINAL_SUPERSEDE['reason']}"


class TestSessionEndDuringControl:
    async def test_a_supersede_behind_a_draining_stop_ends_the_session(
        self, world: BandWorld
    ) -> None:
        """The incident order: an agent-wide STOP whose adapter is slow to
        cancel, then the platform's supersede and its 1001 close."""
        await world.join("r1")
        await world.join("r2")
        world.script.hold_turn("a")
        world.script.hold_turn("b")
        a = await world.say("r1", "a")
        b = await world.say("r2", "b")
        await world.until_told("r1", "turn a")
        await world.until_told("r2", "turn b")
        world.script.hold_controls()

        async with world.running():
            await world.control(ControlMode.STOP, correlation_id="stop-all")
            await world.until_told("r1", "control stop")
            await world.supersede()
            await world.until_told(SESSION_SUBJECT, "disconnected")
            await world.close_connection(CloseCode.GOING_AWAY)
            await world.until_told(SESSION_SUBJECT, ENDED_BY_SUPERSEDE)

            world.script.release_controls()
            await world.until_told("r2", "control stop")
            await world.until_told("r2", "turn b cancelled")

        assert world.server.connection_count == 1
        assert world.story(SESSION_SUBJECT) == ["disconnected", ENDED_BY_SUPERSEDE]
        assert world.story("r1") == [
            f"processing {a}",
            "turn a",
            "control stop",
            "turn a cancelled",
        ]
        assert world.story("r2") == [
            f"processing {b}",
            "turn b",
            "control stop",
            "turn b cancelled",
        ]

    async def test_stopping_the_agent_cancels_a_control_still_applying(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        world.script.hold_controls()
        await world.control(ControlMode.STOP, room_id="r1", correlation_id="stop-1")
        await world.until_told("r1", "control stop")

        await world.stop()

        assert world.story("r1") == [
            "control stop",
            "cleanup started",
            "cleanup done",
            "control stop cancelled",
        ]
