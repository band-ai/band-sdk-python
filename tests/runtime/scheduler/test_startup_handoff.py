"""Singleton startup keeps the platform head, then runs arrivals in platform order."""

from __future__ import annotations

import asyncio

import httpx

from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import WAIT_SECONDS


class TestStartupHandoff:
    async def test_arrivals_during_next_preserve_head_and_receive_order(self) -> None:
        peer = RestPeer()
        peer.post("backlog", at=1)
        answer = peer.answer
        held = asyncio.Event()
        release = asyncio.Event()

        async def barrier(request: httpx.Request) -> httpx.Response:
            response = answer(request)
            if request.url.path.endswith("/messages/next") and len(peer.nexts) == 1:
                held.set()
                await release.wait()
            return response

        peer.answer = barrier
        async with started_session(peer, batching=False) as session:
            async with asyncio.timeout(WAIT_SECONDS):
                await held.wait()
                await session.deliver(
                    peer.post("first-ws", at=2),
                    peer.post("tail-1", at=3),
                    peer.post("tail-2", at=4),
                )
                release.set()
                await session.settled("backlog", "first-ws", "tail-1", "tail-2")
            assert session.turns.ids == [
                ("backlog",),
                ("first-ws",),
                ("tail-1",),
                ("tail-2",),
            ]
