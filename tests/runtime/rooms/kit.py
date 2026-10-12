"""Observable projections of a room's lifecycle."""

from __future__ import annotations

import asyncio
from enum import StrEnum

from band.runtime.rooms import RoomExecutions


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
