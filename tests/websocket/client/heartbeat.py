"""Observable control delivery barriers for heartbeat integration tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from websockets.asyncio.client import ClientConnection

from band.client.streaming import AgentControlPayload, WebSocketClient
from band.client.streaming.client import SupersedePayload, WireEvent
from band.testing.phoenix_server import FakePhoenixServer
from tests.platform.linkclose.kit import CONTROL_TOPIC, control_wire_payload


@dataclass
class ControlProbe:
    arrivals: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)
    sent: int = 0

    async def unexpected_supersede(self, payload: SupersedePayload) -> None:
        raise AssertionError(f"Unexpected supersede: {payload.reason}")

    async def receive(self, payload: AgentControlPayload) -> None:
        await self.arrivals.put(payload.correlation_id)

    async def roundtrip(self, server: FakePhoenixServer) -> None:
        # A delivered push follows the preceding heartbeat reply on the same wire.
        marker = f"probe-{self.sent}"
        self.sent += 1
        await server.push(
            CONTROL_TOPIC, WireEvent.AGENT_CONTROL, control_wire_payload(marker)
        )
        assert await self.arrivals.get() == marker


def socket_of(client: WebSocketClient) -> ClientConnection:
    assert client.client is not None and client.client.connection is not None
    return client.client.connection
