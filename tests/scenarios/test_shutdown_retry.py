"""Stopping the agent while one room's cleanup fails."""

from __future__ import annotations

import pytest

from tests.scenarios.world import BandWorld


class TestShutdownRetry:
    async def test_a_retried_stop_finishes_only_the_failed_room(
        self, world: BandWorld
    ) -> None:
        await world.join("r1")
        await world.join("r2")
        world.script.hold_turn("busy")
        busy = await world.say("r1", "busy")
        await world.until_told("r1", "turn busy")
        world.script.failing_cleanups.add("r2")

        with pytest.raises(ExceptionGroup) as failed:
            await world.stop(timeout=0)
        assert [str(error) for error in failed.value.exceptions] == [
            "cleanup of r2 failed"
        ]
        assert world.runtime.unsettled_rooms == ["r2"]

        world.script.failing_cleanups.clear()
        assert await world.stop(timeout=0) is False
        assert world.runtime.unsettled_rooms == []
        assert world.story("r1") == [
            f"processing {busy}",
            "turn busy",
            "turn busy cancelled",
            "cleanup started",
            "cleanup done",
        ]
        assert world.story("r2") == [
            "cleanup started",
            "cleanup failed",
            "cleanup started",
            "cleanup done",
        ]
