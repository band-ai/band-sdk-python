from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import RoomCleanup, StartGate


@pytest.fixture
def start_gate() -> StartGate:
    return StartGate()


@pytest.fixture
def room_cleanup() -> RoomCleanup:
    return RoomCleanup()


@pytest.fixture
async def rooms(
    start_gate: StartGate, room_cleanup: RoomCleanup
) -> AsyncIterator[RoomExecutions]:
    rooms = RoomExecutions(
        build=lambda room_id: start_gate(room_id, None), release=room_cleanup
    )
    yield rooms
    start_gate.release.set()
    room_cleanup.failing = False
    room_cleanup.release.set()
    rooms.accepting = False
    for room_id in rooms.room_ids():
        await rooms.destroy(room_id)
