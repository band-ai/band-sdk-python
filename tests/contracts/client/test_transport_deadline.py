"""Bound a public HTTP operation across headers and continuous body progress."""

from __future__ import annotations

import asyncio

import pytest

from band.contracts.errors import ContractTransportError
from band.contracts.transport import ContractTransport
from tests.contracts.client.deadline import (
    REQUEST_BODY,
    REQUEST_PATH,
    RESPONSE_CHUNKS,
    Authentication,
    TricklePeer,
    assert_original_request,
)


@pytest.mark.looptime
class TestTransportDeadline:
    @pytest.mark.parametrize(
        ("header_delay", "chunk_delay", "delivered"),
        [(0.0, 0.375, 2), (0.75, 0.1875, 1)],
        ids=["continuous-body", "headers-spend-body-budget"],
    )
    async def test_trickling_response_cannot_refresh_total_deadline(
        self, header_delay: float, chunk_delay: float, delivered: int
    ) -> None:
        peer = TricklePeer(header_delay=header_delay, chunk_delay=chunk_delay)
        async with (
            peer.client() as http,
            ContractTransport(peer.connection, http=http) as transport,
        ):
            started = peer.clock.time()
            with pytest.raises(ContractTransportError, match="delivery is unknown"):
                await transport.request("POST", REQUEST_PATH, body=REQUEST_BODY)
            assert peer.clock.time() - started == 1.0
            assert peer.stream.closed_at == started + 1.0
            assert peer.stream.delivered == list(RESPONSE_CHUNKS[:delivered])
            assert_original_request(peer)
            await transport.aclose()
            assert not http.is_closed

    @pytest.mark.parametrize("authentication", ["band-api-key", "bearer"])
    async def test_complete_response_before_deadline_preserves_request(
        self, authentication: Authentication
    ) -> None:
        peer = TricklePeer(
            header_delay=0.125,
            chunk_delay=0.125,
            authentication=authentication,
        )
        async with (
            peer.client() as http,
            ContractTransport(peer.connection, http=http) as transport,
        ):
            started = peer.clock.time()
            response = await transport.request("POST", REQUEST_PATH, body=REQUEST_BODY)
            assert response == {"accepted": True}
            assert peer.clock.time() - started == 0.5
            assert peer.stream.closed_at == started + 0.5
            assert peer.stream.delivered == list(RESPONSE_CHUNKS)
            assert_original_request(peer)

    async def test_caller_cancellation_closes_stream_without_translation(self) -> None:
        peer = TricklePeer(header_delay=0.0, chunk_delay=0.375)
        async with (
            peer.client() as http,
            ContractTransport(peer.connection, http=http) as transport,
        ):
            started = peer.clock.time()
            request = asyncio.create_task(
                transport.request("POST", REQUEST_PATH, body=REQUEST_BODY)
            )
            try:
                await peer.stream.read_started.wait()
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                assert request.cancelled()
                assert peer.stream.closed_at == started
                assert peer.stream.delivered == []
                assert_original_request(peer)
            finally:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
