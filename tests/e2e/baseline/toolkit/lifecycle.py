"""Observe completed room cleanup without changing the adapter's lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from band.core.protocols import FrameworkAdapter


@dataclass
class RoomCleanup:
    room_id: str
    completed: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self, *, deadline_s: float) -> None:
        try:
            async with asyncio.timeout(deadline_s):
                await self.completed.wait()
        except TimeoutError:
            raise TimeoutError(
                f"Room {self.room_id} cleanup did not complete before rejoin"
            ) from None


@contextmanager
def observe_room_cleanup(
    adapter: FrameworkAdapter, room_id: str
) -> Iterator[RoomCleanup]:
    cleanup = RoomCleanup(room_id)
    original = adapter.on_cleanup

    async def observed(room: str) -> None:
        await original(room)
        if room == room_id:
            cleanup.completed.set()

    with patch.object(adapter, "on_cleanup", observed):
        yield cleanup
