"""Own local peers for channel-leave lifecycle checks."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from websockets.asyncio.server import serve

from band.platform.link import BandLink
from tests.websocket.client.leaving.kit import LeavePeer, make_link


@pytest_asyncio.fixture(loop_scope="function")
async def leave_peer() -> AsyncIterator[LeavePeer]:
    peer = LeavePeer()
    async with serve(peer.handle, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        peer.url = f"ws://127.0.0.1:{port}/socket/websocket"
        try:
            yield peer
        finally:
            peer.released.set()


@pytest_asyncio.fixture(loop_scope="function")
async def link(leave_peer: LeavePeer) -> AsyncIterator[BandLink]:
    link = make_link(leave_peer.url)
    try:
        await link.connect()
        yield link
    finally:
        leave_peer.released.set()
        await link.disconnect()
