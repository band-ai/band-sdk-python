"""A failed cleanup keeps its execution owned for the next stop to retry."""

from __future__ import annotations

import pytest

from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import RoomCleanup, StartGate
from tests.runtime.rooms.kit import Phase, phase


class TestCleanupFailure:
    async def test_a_failed_cleanup_raises_and_the_retry_finishes_it(
        self, rooms: RoomExecutions, room_cleanup: RoomCleanup
    ) -> None:
        await rooms.admit("room-1")
        room_cleanup.failing = True

        with pytest.raises(RuntimeError, match="room cleanup failed"):
            await rooms.destroy("room-1")
        assert phase(rooms, "room-1") == Phase.OWNED

        room_cleanup.failing = False
        assert await rooms.destroy("room-1") is True
        assert phase(rooms, "room-1") == Phase.ABSENT
        assert room_cleanup.rooms == ["room-1"]

    async def test_an_interrupted_stop_is_reported_as_not_graceful(
        self, rooms: RoomExecutions, start_gate: StartGate
    ) -> None:
        start_gate.graceful = False
        await rooms.admit("room-1")

        assert await rooms.destroy("room-1") is False
