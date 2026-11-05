"""Own real HTTP clients and adapters around the local SSE peer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from band.adapters.opencode import OpencodeAdapterConfig
from band.integrations.opencode.client import HttpOpencodeClient
from tests.adapters.opencode.helpers import ObservedOpencodeAdapter
from tests.adapters.opencode.stopconfirmation.peer import OpencodePeer


@pytest.fixture
def peer() -> OpencodePeer:
    return OpencodePeer()


@pytest_asyncio.fixture(loop_scope="function")
async def adapter(peer: OpencodePeer) -> AsyncIterator[ObservedOpencodeAdapter]:
    clients: list[HttpOpencodeClient] = []

    def client_factory(config: OpencodeAdapterConfig) -> HttpOpencodeClient:
        client = HttpOpencodeClient(
            base_url="http://opencode.invalid",
            transport=httpx.MockTransport(peer.request),
        )
        clients.append(client)
        return client

    adapter = ObservedOpencodeAdapter(client_factory=client_factory, emit=())
    await adapter.on_started("OpenCode", "local transport peer")
    try:
        yield adapter
    finally:
        peer.first_prompt.released.set()
        try:
            await adapter.cleanup_all()
        finally:
            for task in adapter.background_tasks:
                task.cancel()
            await asyncio.gather(*adapter.background_tasks, return_exceptions=True)
            await adapter.backend_turns.drain()
            await asyncio.gather(*(client.close() for client in clients))
