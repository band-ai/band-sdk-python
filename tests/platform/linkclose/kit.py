"""Shared helpers for driving a live BandLink's closes and supersedes over a
real (fake-server-backed) wire."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band_sdk_core import AgentTopicKind, DeadReason

from band.client.streaming.client import WebSocketDisconnectReason
from band.platform.link import BandLink

AGENT_ID = "agent-123"
CONTROL_TOPIC = AgentTopicKind.Control.topic(AGENT_ID)
HANG_GUARD_S = 5.0

SUPERSEDED = WebSocketDisconnectReason(
    reason="session.already_connected",
    message="Superseded by a newer session",
    retryable=False,
    retry_after=15,
    target_socket_id="agent_socket:agent-123",
    correlation_id="evict-1",
    dead_reason=DeadReason.Classified,
)


def make_link(server_url: str) -> BandLink:
    return BandLink(
        agent_id=AGENT_ID,
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
    )


@asynccontextmanager
async def connected(link: BandLink) -> AsyncIterator[BandLink]:
    await link.connect()
    try:
        yield link
    finally:
        await link.disconnect()


@asynccontextmanager
async def running(link: BandLink) -> AsyncIterator[asyncio.Task[None]]:
    """``run_forever`` as a task the host is already blocked on."""
    run = asyncio.create_task(link.run_forever(install_signal_handlers=False))
    try:
        yield run
    finally:
        run.cancel()
        await asyncio.wait({run})
