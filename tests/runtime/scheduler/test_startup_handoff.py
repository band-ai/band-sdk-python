"""Singleton startup keeps the platform head, then hands off to local FIFO."""

from __future__ import annotations

import asyncio

import httpx

from tests.runtime.delivery.kit import RestPeer, started_session


class TestStartupHandoff:
    async def test_arrivals_during_next_preserve_head_and_receive_order(self) -> None:
        peer = RestPeer()
        peer.post("backlog", at=5)
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
            async with asyncio.timeout(3):
                await held.wait()
                await session.deliver(
                    peer.post("first-ws", at=1),
                    peer.post("tail-z", at=3),
                    peer.post("tail-a", at=2),
                )
                release.set()
                await session.settled("backlog", "first-ws", "tail-z", "tail-a")
            assert session.turns.ids == [
                ("backlog",),
                ("first-ws",),
                ("tail-z",),
                ("tail-a",),
            ]
            assert (len(peer.nexts), len(peer.reads)) == (2, 0)
