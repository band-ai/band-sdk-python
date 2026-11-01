"""Real released-client lifetime over a gated external SSE transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.adapters.letta.compaction.peer import CompactionStream

letta = pytest.importorskip("letta_client")


@pytest.fixture
async def streamed_client() -> AsyncIterator[tuple[Any, CompactionStream]]:
    peer = CompactionStream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(peer.respond)
    ) as transport:
        async with letta.AsyncLetta(
            api_key="test",
            base_url="http://letta.test",
            max_retries=0,
            http_client=transport,
        ) as client:
            try:
                yield client, peer
            finally:
                peer.release.set()
