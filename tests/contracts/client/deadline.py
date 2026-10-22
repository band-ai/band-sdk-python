"""Deliver finite response chunks on the test's simulated event-loop clock."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from looptime import LoopTimeEventLoop
from pydantic import SecretStr

from band.contracts.models import ContractConnection

Authentication = Literal["band-api-key", "bearer"]
REQUEST_PATH = "/v1/proposals"
REQUEST_BODY = (
    b'{"id":"stable","session":"session","kind":"answer",'
    b'"payload":{"value":42},"expected_version":0}'
)
CREDENTIAL = "a" * 32
RESPONSE_CHUNKS = (b'{"accepted":', b"true", b"}")


class TrickleStream(httpx.AsyncByteStream):
    def __init__(self, chunk_delay: float) -> None:
        self.chunk_delay = chunk_delay
        self.read_started = asyncio.Event()
        self.delivered: list[bytes] = []
        self.closed_at: float | None = None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in RESPONSE_CHUNKS:
            self.read_started.set()
            await asyncio.sleep(self.chunk_delay)
            self.delivered.append(chunk)
            yield chunk

    async def aclose(self) -> None:
        self.closed_at = asyncio.get_running_loop().time()


class TricklePeer:
    def __init__(
        self,
        *,
        header_delay: float,
        chunk_delay: float,
        authentication: Authentication = "band-api-key",
    ) -> None:
        loop = asyncio.get_running_loop()
        assert isinstance(loop, LoopTimeEventLoop) and loop.looptime_on
        self.clock = loop
        self.header_delay = header_delay
        self.stream = TrickleStream(chunk_delay)
        self.requests: list[httpx.Request] = []
        self.connection = ContractConnection(
            url="https://platform.test",
            credential=SecretStr(CREDENTIAL),
            authentication=authentication,
            timeout=1.0,
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle), trust_env=False
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        await asyncio.sleep(self.header_delay)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(sum(map(len, RESPONSE_CHUNKS))),
            },
            stream=self.stream,
        )


def assert_original_request(peer: TricklePeer) -> None:
    """Require one authenticated send of the exact staged proposal bytes."""
    assert len(peer.requests) == 1
    request = peer.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://platform.test" + REQUEST_PATH
    assert request.content == REQUEST_BODY
    assert request.headers["content-type"] == "application/json"
    assert request.headers["accept"] == "application/json"
    if peer.connection.authentication == "bearer":
        assert request.headers["authorization"] == f"Bearer {CREDENTIAL}"
        assert "x-api-key" not in request.headers
    else:
        assert request.headers["x-api-key"] == CREDENTIAL
        assert "authorization" not in request.headers
