"""Shared helpers for driving a live BandLink's closes and supersedes over a
real (fake-server-backed) wire."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from band_sdk_core import AgentTopicKind, SessionPolicy

from band.client.streaming import (
    AgentControlPayload,
    ControlMode,
    SupersedePayload,
    WebSocketClient,
)
from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from tests.conftest import TERMINAL_SUPERSEDE
from tests.sessionpolicies import millisecond_backoff

AGENT_ID = "agent-123"
CONTROL_TOPIC = AgentTopicKind.Control.topic(AGENT_ID)
HANG_GUARD_S = 5.0


def make_link(
    server_url: str, *, session_policy: SessionPolicy | None = None
) -> BandLink:
    """A link to ``server_url``; it reconnects after a millisecond unless a
    test passes its own policy."""
    return BandLink(
        agent_id=AGENT_ID,
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
        session_policy=session_policy or millisecond_backoff(),
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


@dataclass
class ParkedSupersede:
    """A supersede push the link received, held before its session sees it."""

    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    handled: asyncio.Event = field(default_factory=asyncio.Event)


def park_supersedes(link: BandLink) -> ParkedSupersede:
    """Hold each supersede push until ``released``. Arm before ``connect()``,
    which binds the handler.

    Wraps the handler because nothing public reaches the one window this
    holds open: phoenix has already dispatched the push but its callback task
    has not run when ``disconnect()`` begins; after that, phoenix drops pushes.
    """
    parked = ParkedSupersede()
    on_supersede = link._on_supersede

    async def park(ws: WebSocketClient, payload: SupersedePayload) -> None:
        parked.arrived.set()
        await parked.released.wait()
        await on_supersede(ws, payload)
        parked.handled.set()

    link._on_supersede = park
    return parked


def drain_events(link: BandLink) -> list[PlatformEvent]:
    """Every event the link has queued that the host has not read yet.

    Reads the queue directly as the observation seam: the public stream only
    awaits, and cancelling a zero-wait read would race the next event.
    """
    events: list[PlatformEvent] = []
    while not link._event_queue.empty():
        events.append(link._event_queue.get_nowait())
    return events


async def handle_supersede(link: BandLink) -> None:
    """Hand the link a terminal supersede as its control topic would, in
    states where the platform could not push one."""
    assert link._ws is not None
    await link._on_supersede(
        link._ws, SupersedePayload.model_validate(TERMINAL_SUPERSEDE)
    )
