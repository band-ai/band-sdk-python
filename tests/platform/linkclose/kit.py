"""Shared helpers for driving a live BandLink's closes and supersedes over a
real (fake-server-backed) wire."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager

from band_sdk_core import AgentTopicKind, DeadReason

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


@asynccontextmanager
async def during_the_next_backoff(
    link: BandLink, action: Callable[[], Coroutine[object, object, None]]
) -> AsyncIterator[None]:
    """Run ``action`` once the transport has read its reconnect decision for
    the next close, i.e. while it waits out the backoff before reconnecting.

    The pinned transport reads ``auto_reconnect`` synchronously after the
    disconnect callback returns, so a task the callback starts runs only
    once that backoff has begun. Enter before ``connect()``, which binds the
    callback.
    """
    on_disconnected = link._on_disconnected
    acted: list[asyncio.Task[None]] = []

    async def then_act(error: Exception | None) -> None:
        await on_disconnected(error)
        if not acted:
            acted.append(asyncio.create_task(action()))

    link._on_disconnected = then_act
    try:
        yield
    finally:
        await asyncio.gather(*acted)


async def handle_supersede(link: BandLink) -> None:
    assert link._ws is not None
    await link._on_supersede(
        link._ws, SupersedePayload.model_validate(TERMINAL_SUPERSEDE)
    )
