"""Gates around real BandLink reconciliation and disconnect callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from band_sdk_core import chat_room_topic, room_participants_topic

from band.client.streaming import WebSocketClient
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


@contextmanager
def initial_retries() -> Iterator[asyncio.Queue[float]]:
    """Observe the real Session decision before its initial-connect backoff."""
    decisions: asyncio.Queue[float] = asyncio.Queue()
    original = WebSocketClient._resolve_failed_connect_attempt

    async def observe(client: WebSocketClient, error: Exception, epoch: int) -> float:
        delay = await original(client, error, epoch)
        await decisions.put(delay)
        return delay

    with patch.object(WebSocketClient, "_resolve_failed_connect_attempt", observe):
        yield decisions
