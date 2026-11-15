"""A restored room can make a 404 look like deletion of an existing original."""

from __future__ import annotations

import asyncio

import httpx

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import WAIT_SECONDS


class TestAccessChurn:
    async def test_restored_access_before_the_probe_retires_the_original(self) -> None:
        peer = RestPeer()
        peer.post("original")
        rejected = asyncio.Event()
        release_response = asyncio.Event()
        answer = peer.answer

        async def barrier(request: httpx.Request) -> httpx.Response:
            response = answer(request)
            if request.url.path.endswith(f"/original/{DeliveryStatus.FAILED}"):
                rejected.set()
                await release_response.wait()
            return response

        peer.answer = barrier
        async with started_session(
            peer, gate=True, retries=3, fail_on=frozenset({"original"})
        ) as session:
            await session.until(lambda: session.turns.ids == [("original",)])
            peer.participant = False
            session.turns.gate = False
            session.turns.release.set()
            async with asyncio.timeout(WAIT_SECONDS):
                await rejected.wait()
                peer.participant = True
                release_response.set()
                await session.deliver(peer.post("follower"))
                await session.settled("follower")

        assert session.turns.ids == [("original",), ("follower",)]
        assert peer.status_of("original") is DeliveryStatus.PROCESSING
        assert len(peer.writes_of(DeliveryStatus.FAILED, "original")) == 1
