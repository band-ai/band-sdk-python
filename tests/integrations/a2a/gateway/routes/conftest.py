"""Official-handler clients for gateway route tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from httpx import ASGITransport

from tests.integrations.a2a.gateway.serverkit import (
    build_multi_peer_server,
    build_server,
)


@pytest_asyncio.fixture
async def gateway_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=build_server()._build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def multi_peer_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=build_multi_peer_server()._build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
