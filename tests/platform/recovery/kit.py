"""Gates around real BandLink reconciliation and disconnect callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from band_sdk_core import chat_room_topic, room_participants_topic

from band.platform.link import BandLink

ROOM_ID = "room-123"
ROOM_TOPIC = chat_room_topic(ROOM_ID)
ROOM_TOPICS = frozenset({ROOM_TOPIC, room_participants_topic(ROOM_ID)})


def observe_disconnects(link: BandLink) -> asyncio.Queue[None]:
    disconnected: asyncio.Queue[None] = asyncio.Queue()
    original = link._on_disconnected

    async def observe(error: Exception | None) -> None:
        await original(error)
        await disconnected.put(None)

    link._on_disconnected = observe
    return disconnected


@dataclass
class ReconciliationGate:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)


@asynccontextmanager
async def hold_reconciliation(link: BandLink) -> AsyncIterator[ReconciliationGate]:
    original = link._drain_reconciliation
    gate = ReconciliationGate()

    async def held() -> None:
        gate.entered.set()
        await gate.released.wait()
        await original()

    link._drain_reconciliation = held
    try:
        yield gate
    finally:
        gate.released.set()
        link._drain_reconciliation = original
