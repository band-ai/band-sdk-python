"""Shared Pydantic AI fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from tests.adapters.pydanticai.kit import (
    instrument_all_restored,
    mock_pydantic_agent,
    mock_tools,
    sample_message,
    trace_capture,
)
from tests.e2e.baseline.toolkit.activitywork import ActivityWork
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer

__all__ = [
    "instrument_all_restored",
    "mock_pydantic_agent",
    "mock_tools",
    "sample_message",
    "trace_capture",
]


@pytest.fixture
async def activity_work() -> AsyncIterator[tuple[ActivityWork, HeldLookupPeer]]:
    """The baseline's activity tool over a lookup that answers at once."""
    peer = HeldLookupPeer()
    peer.release.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.respond)) as client:
        work = ActivityWork(client, "https://lookup.invalid", deadline_s=10)
        async with work.releasing_on_exit():
            yield work, peer
