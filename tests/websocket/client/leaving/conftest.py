"""Own local peers for channel-leave lifecycle checks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from websockets.asyncio.server import serve

from band.client.streaming import ContactAddedPayload, WebSocketClient
from band.platform.link import BandLink
from tests.websocket.client.leaving.kit import LeavePeer, make_link


@pytest_asyncio.fixture(loop_scope="function")
async def leave_peer() -> AsyncIterator[LeavePeer]:
    peer = LeavePeer()
    peer.close_released.set()
    async with serve(peer.handle, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        peer.url = f"ws://127.0.0.1:{port}/socket/websocket"
        try:
            yield peer
        finally:
            peer.released.set()
            peer.close_released.set()


@pytest_asyncio.fixture(loop_scope="function")
async def link(leave_peer: LeavePeer) -> AsyncIterator[BandLink]:
    link = make_link(leave_peer.url)
    try:
        await link.connect()
        yield link
    finally:
        leave_peer.released.set()
        leave_peer.close_released.set()
        await link.disconnect()


@pytest_asyncio.fixture(loop_scope="function")
async def ws(leave_peer: LeavePeer) -> AsyncIterator[WebSocketClient]:
    async with WebSocketClient(leave_peer.url, "test-key") as client:
        try:
            yield client
        finally:
            leave_peer.released.set()
            leave_peer.close_released.set()


@pytest.fixture
def contact_events() -> asyncio.Queue[ContactAddedPayload]:
    return asyncio.Queue()


@pytest.fixture
def contact_handlers(
    contact_events: asyncio.Queue[ContactAddedPayload],
) -> dict[str, Callable[..., Awaitable[None]]]:
    return {
        "on_contact_request_received": contact_events.put,
        "on_contact_request_updated": contact_events.put,
        "on_contact_added": contact_events.put,
        "on_contact_removed": contact_events.put,
    }
