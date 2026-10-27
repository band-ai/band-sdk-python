"""Own room serialization, forwarding capacity and cancellation until work settles."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field


@dataclass
class RoomWork:
    """Keep one serialization gate for every accepted holder and waiter."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: set[asyncio.Task[object]] = field(default_factory=set)


class RoomForwards:
    """Retain each room only while it owns accepted work, independent of membership."""

    def __init__(self, max_concurrency: int) -> None:
        self._rooms: dict[str | None, RoomWork] = {}
        self._capacity = asyncio.Semaphore(max_concurrency)

    @contextmanager
    def accepted(self, room_id: str | None) -> Iterator[RoomWork]:
        """Register before any suspension; release ownership after settlement."""
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Forwarding admission requires an asyncio task")
        room = self._rooms.get(room_id)
        if room is None:
            room = RoomWork()
            self._rooms[room_id] = room
        room.tasks.add(task)
        try:
            yield room
        finally:
            room.tasks.remove(task)
            if not room.tasks:
                del self._rooms[room_id]

    @asynccontextmanager
    async def ready(self, room: RoomWork) -> AsyncIterator[None]:
        """Reserve agent capacity only after reaching the front of a room's work."""
        async with room.lock, self._capacity:
            yield

    def active_rooms(self) -> frozenset[str | None]:
        """Rooms that currently own accepted work."""
        return frozenset(self._rooms)

    def cancel(self, room_id: str) -> int:
        """Cancel the room's running and queued tasks without discarding ownership."""
        room = self._rooms.get(room_id)
        if room is None:
            return 0
        pending = tuple(task for task in room.tasks if not task.done())
        for task in pending:
            task.cancel()
        return len(pending)
