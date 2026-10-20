"""Shared helpers for driving a live BandLink's closes and supersedes over a
real (fake-server-backed) wire."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from band_sdk_core import AgentTopicKind, DeadReason
from phoenix_channels_python_client.client import PHXChannelsClient

from band.client.streaming import AgentControlPayload, ControlMode
from band.client.streaming.client import (
    SupersedePayload,
    WebSocketClient,
    WebSocketDisconnectReason,
)
from band.platform.link import BandLink
from tests.conftest import TERMINAL_SUPERSEDE

AGENT_ID = "agent-123"
CONTROL_TOPIC = AgentTopicKind.Control.topic(AGENT_ID)
HANG_GUARD_S = 5.0

SUPERSEDED = WebSocketDisconnectReason(
    **TERMINAL_SUPERSEDE, dead_reason=DeadReason.Classified
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


def control_cancellations(records: list[logging.LogRecord]) -> list[str]:
    """What ``disconnect()`` reported cancelling from the control lane."""
    return [
        r.getMessage() for r in records if r.msg.startswith("Cancelling %d unfinished")
    ]


def agent_control(mode: ControlMode, correlation_id: str) -> AgentControlPayload:
    return AgentControlPayload(
        mode=mode, scope="agent", agent_id=AGENT_ID, correlation_id=correlation_id
    )


def control_wire_payload(correlation_id: str) -> dict[str, object]:
    """An ``agent.control`` push body as the platform sends it."""
    return {
        "mode": ControlMode.INTERRUPT,
        "scope": "agent",
        "agent_id": AGENT_ID,
        "execution_id": None,
        "room_id": None,
        "correlation_id": correlation_id,
    }


@asynccontextmanager
async def during_connect(
    link: BandLink, action: Callable[[], Coroutine[object, object, None]]
) -> AsyncIterator[None]:
    """Run ``action`` once the control channel is joined and before
    ``connect()`` returns, while the link has no ``_ws`` yet. Enter before
    ``connect()``."""
    join = link._join_agent_control_channel

    async def join_then_act(ws: WebSocketClient) -> None:
        await join(ws)
        await action()

    link._join_agent_control_channel = join_then_act
    try:
        yield
    finally:
        del link._join_agent_control_channel


def transport_client(link: BandLink) -> PHXChannelsClient:
    """The live transport under a connected link."""
    assert link._ws is not None and link._ws.client is not None
    return link._ws.client


@dataclass
class ParkedBackoff:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    def release(self) -> None:
        self.released.set()


@asynccontextmanager
async def parked_backoff(link: BandLink) -> AsyncIterator[ParkedBackoff]:
    """Park the transport's next reconnect backoff before it starts counting.

    It ends only on ``release()`` or when the transport shuts down, so what a
    test does meanwhile cannot race the backoff's expiry. Enter after
    ``connect()``.
    """
    client = transport_client(link)
    backoff_wait = client._wait_for_shutdown_or_timeout
    backoff = ParkedBackoff()

    async def parked(delay_s: float) -> None:
        client._wait_for_shutdown_or_timeout = backoff_wait
        backoff.entered.set()
        waiters = [
            asyncio.create_task(event.wait())
            for event in (backoff.released, client._shutdown_event)
        ]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
        await backoff_wait(delay_s)

    client._wait_for_shutdown_or_timeout = parked
    try:
        yield backoff
    finally:
        client._wait_for_shutdown_or_timeout = backoff_wait


async def handle_supersede(link: BandLink) -> None:
    assert link._ws is not None
    await link._on_supersede(
        link._ws, SupersedePayload.model_validate(TERMINAL_SUPERSEDE)
    )
