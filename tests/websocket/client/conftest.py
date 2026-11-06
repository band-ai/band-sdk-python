"""Fixtures for real WebSocket client lifecycle boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from band.client.streaming import WebSocketClient
from band.testing import FakePhoenixServer, fake_phoenix_server
from tests.identifiers import AGENT_ID
from tests.sessionpolicies import session_policy
from tests.websocket.client.disconnect import DisconnectGate


@pytest.fixture
def disconnect_gate() -> Iterator[DisconnectGate]:
    gate = DisconnectGate()
    try:
        yield gate
    finally:
        gate.released.set()


@pytest_asyncio.fixture(loop_scope="function")
async def disconnect_client(
    disconnect_gate: DisconnectGate,
) -> AsyncIterator[tuple[FakePhoenixServer, WebSocketClient]]:
    async with fake_phoenix_server() as server:
        yield (
            server,
            WebSocketClient(
                server.url,
                "test-key",
                AGENT_ID,
                on_disconnect=disconnect_gate.hold,
                session_policy=session_policy(),
            ),
        )
