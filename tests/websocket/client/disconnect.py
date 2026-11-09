"""Gate a real client's disconnect callback before its session decision."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from band.client.streaming import SupersedePayload, WebSocketClient


@dataclass
class DisconnectGate:
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    waiting: asyncio.Event = field(default_factory=asyncio.Event)

    async def hold(self, error: Exception | None) -> None:
        self.ready.set()
        await self.released.wait()

    async def wait_for_decision(self, client: WebSocketClient) -> None:
        self.waiting.set()
        await client.wait_for_disconnect_decision()


async def reject_supersede(payload: SupersedePayload) -> None:
    pytest.fail("No supersede was sent")
