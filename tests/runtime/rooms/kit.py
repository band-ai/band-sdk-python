"""Observable projections of a room's lifecycle."""

from __future__ import annotations

import asyncio
from enum import StrEnum

from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import StartGate


class Phase(StrEnum):
    """What the rest of the runtime can see of one room."""

    SERVING = "serving"
    OWNED = "owned"
    """Owns an execution that is not serving: startup or cleanup unsettled."""
    PENDING = "pending"
    """Tracked, owning no execution."""
    ABSENT = "absent"


def phase(rooms: RoomExecutions, room_id: str) -> Phase:
    if rooms.serving(room_id):
        return Phase.SERVING
    if room_id in rooms.unsettled:
        return Phase.OWNED
    if room_id in rooms.room_ids():
        return Phase.PENDING
    return Phase.ABSENT


async def settle(rooms: RoomExecutions, room_id: str) -> None:
    """Let the room's convergence finish, whatever its outcome."""
    if (pending := rooms.pending(room_id)) is not None:
        await asyncio.wait({pending})


async def deliver_first_of_two(
    rooms: RoomExecutions, start_gate: StartGate
) -> asyncio.Task[object]:
    """Start a room holding two events and pause while the first is being
    delivered; returns the admission still waiting on it."""
    start_gate.hold()
    admitting = asyncio.create_task(rooms.admit("room-1"))
    await start_gate.entered.wait()
    rooms.hold("room-1", "early")
    rooms.hold("room-1", "second")
    start_gate.hold_delivery()
    start_gate.release.set()
    await start_gate.delivering.wait()
    return admitting
