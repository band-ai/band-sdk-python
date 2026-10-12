"""Signals during a step never start a second one for the same room."""

from __future__ import annotations

import asyncio

from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import StartGate
from tests.runtime.rooms.kit import Phase, phase


class TestSingleStep:
    async def test_signals_during_a_held_start_join_it(
        self, rooms: RoomExecutions, start_gate: StartGate
    ) -> None:
        start_gate.hold()
        admitting = asyncio.create_task(rooms.admit("room-1"))
        await start_gate.entered.wait()

        rooms.request_admission("room-1")
        rooms.converge("room-1")
        rooms.converge_unserved()
        start_gate.release.set()

        assert await admitting is start_gate.built[0]
        assert len(start_gate.built) == 1
        assert phase(rooms, "room-1") == Phase.SERVING
