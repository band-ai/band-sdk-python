"""A stop retry keeps reporting a room an earlier attempt cancelled."""

from __future__ import annotations

import pytest

from band.runtime.runtime import AgentRuntime
from tests.runtime.agentruntime.kit import StartGate, joined
from tests.runtime.helpers import RoomCleanup


class TestStopRetry:
    """A stop retry keeps reporting a room an earlier attempt cancelled."""

    async def test_stop_retry_keeps_an_earlier_cancelled_room_reported(
        self,
        held_runtime: AgentRuntime,
        start_gate: StartGate,
        room_cleanup: RoomCleanup,
    ):
        await joined(held_runtime, "room-1")
        await joined(held_runtime, "room-2")
        start_gate.graceful = False
        room_cleanup.failing_rooms = {"room-2"}
        with pytest.raises(ExceptionGroup):
            await held_runtime.stop()

        start_gate.graceful = True
        room_cleanup.failing_rooms = set()
        assert await held_runtime.stop() is False
        assert sorted(room_cleanup.rooms) == ["room-1", "room-2"]
