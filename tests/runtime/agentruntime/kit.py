"""Shared doubles and room-lifecycle steps for AgentRuntime tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from band.runtime.runtime import AgentRuntime


async def settled(runtime: AgentRuntime, room_id: str) -> None:
    """Let the room's convergence finish, whatever its outcome."""
    if (pending := runtime.rooms.pending(room_id)) is not None:
        await asyncio.wait({pending})


async def joined(runtime: AgentRuntime, room_id: str) -> None:
    """Join as presence does, then let the requested admission settle."""
    await runtime._on_room_joined(room_id, {})
    await settled(runtime, room_id)


async def left(runtime: AgentRuntime, room_id: str) -> None:
    """Leave as presence does, then let the requested cleanup settle."""
    await runtime._on_room_left(room_id)
    await settled(runtime, room_id)


async def until_admitted(runtime: AgentRuntime, room_id: str) -> None:
    """Let the convergence that admits the room finish."""
    await settled(runtime, room_id)


class QueueLink:
    """Feeds platform events to the real presence consumer."""

    def __init__(self, link: MagicMock) -> None:
        self.events: asyncio.Queue[object] = asyncio.Queue()

        async def stream():
            while True:
                yield await self.events.get()

        link.__aiter__ = lambda _self: stream()
