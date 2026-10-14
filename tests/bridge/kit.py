"""Shared runners, events and forwarders for the bridge tests."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from bridge_core.bridge import AgentRunner
from bridge_core.config import ReconnectConfig
from bridge_core.forwarder import Forwarder

from band.client.streaming import AgentControlPayload
from band.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    RoomAddedEvent,
    RoomRemovedEvent,
)
from band.runtime.types import PlatformMessage
from tests.bridge.conftest import FakeForwarder, make_http_agent, make_link_mock


def make_message_event(
    message_id: str = "m1",
    room_id: str = "r1",
    sender_id: str = "sender",
    content: str = "hello",
) -> MessageEvent:
    payload = MagicMock()
    payload.id = message_id
    payload.content = content
    payload.sender_id = sender_id
    payload.model_dump = MagicMock(
        return_value={"id": message_id, "content": content, "sender_id": sender_id}
    )
    return MessageEvent(
        type="message_created",
        room_id=room_id,
        payload=payload,
        raw={"event": "message_created", "id": message_id},
    )


def make_room_added_event(room_id: str = "r1") -> RoomAddedEvent:
    payload = MagicMock()
    payload.model_dump = MagicMock(return_value={"id": room_id})
    return RoomAddedEvent(
        type="room_added",
        room_id=room_id,
        payload=payload,
        raw={"event": "room_added"},
    )


def make_room_removed_event(room_id: str = "r1") -> RoomRemovedEvent:
    payload = MagicMock()
    payload.model_dump = MagicMock(return_value={"id": room_id})
    return RoomRemovedEvent(
        type="room_removed",
        room_id=room_id,
        payload=payload,
        raw={"event": "room_removed"},
    )


def make_participant_added_event(
    room_id: str = "r1", participant_id: str = "p1"
) -> ParticipantAddedEvent:
    payload = MagicMock()
    payload.id = participant_id
    payload.model_dump = MagicMock(return_value={"id": participant_id})
    return ParticipantAddedEvent(
        type="participant_added",
        room_id=room_id,
        payload=payload,
        raw={"event": "participant_added"},
    )


AGENT_ID = "agent-1"


def build_runner(
    *,
    agent_id: str = AGENT_ID,
    forwarder: Forwarder | None = None,
    link: MagicMock | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> tuple[AgentRunner, FakeForwarder, MagicMock]:
    fwd = forwarder if forwarder is not None else FakeForwarder()
    lnk = link if link is not None else make_link_mock()
    runner = AgentRunner(
        agent_config=make_http_agent(agent_id=agent_id),
        ws_url="wss://test",
        rest_url="https://test",
        forwarder=fwd,
        reconnect=ReconnectConfig(),
        shutdown_event=shutdown_event or asyncio.Event(),
        link=lnk,
    )
    return runner, fwd, lnk  # type: ignore[return-value]


def make_platform_message(
    message_id: str = "m1",
    room_id: str = "r1",
    sender_id: str = "sender",
    content: str = "hello",
) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name="Someone",
        message_type="user",
        metadata={},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def rehydrating_runner(
    forwarder: Forwarder, *backlog: PlatformMessage
) -> tuple[AgentRunner, MagicMock]:
    """A runner whose rooms each hold one backlog message for ``/next``."""
    runner, _, link = build_runner(forwarder=forwarder)
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=[SimpleNamespace(id=m.room_id) for m in backlog])
    )
    link.get_next_message.side_effect = list(backlog)
    link.__anext__ = AsyncMock(side_effect=StopAsyncIteration())
    return runner, link


class OrderTrackingForwarder:
    """Forwarder that records start/end of each forward, with a delay.

    Lets us observe whether forwards serialize or overlap by examining the
    order of (start, end) events across calls.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay
        self.events: list[tuple[str, str]] = []  # (kind, msg_id)
        self.closed = False

    async def forward(self, payload: dict[str, Any]) -> None:
        msg_id = (payload.get("payload") or {}).get("id", "?")
        self.events.append(("start", msg_id))
        await asyncio.sleep(self._delay)
        self.events.append(("end", msg_id))

    async def close(self) -> None:
        self.closed = True


def control_signal(
    mode: str, *, scope: str, room_id: str | None, **kw: Any
) -> AgentControlPayload:
    return AgentControlPayload(
        mode=mode, scope=scope, agent_id="agent-1", room_id=room_id, **kw
    )


class HangingForwarder:
    """Hangs on the first forward() until cancelled; forwards normally after.

    ``started`` lets a test wait until the forward call has actually begun
    before issuing a control signal, avoiding a race between task creation
    and the signal arriving before there's anything in flight to cancel.
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.hang = True
        self.forwarded: list[dict[str, Any]] = []

    async def forward(self, payload: dict[str, Any]) -> None:
        if self.hang:
            self.started.set()
            await asyncio.sleep(120)
        self.forwarded.append(payload)

    async def close(self) -> None:
        return


class MultiRoomHangingForwarder:
    """Hangs forever, independently, for each of a fixed set of rooms, and
    forwards any other room's payload at once."""

    def __init__(self, rooms: list[str]) -> None:
        self.started: dict[str, asyncio.Event] = {r: asyncio.Event() for r in rooms}
        self.forwarded: list[dict[str, Any]] = []

    async def forward(self, payload: dict[str, Any]) -> None:
        room_id = payload.get("room_id")
        if room_id in self.started:
            self.started[room_id].set()
            await asyncio.sleep(120)
        self.forwarded.append(payload)

    async def close(self) -> None:
        return


async def await_cancelled(task: asyncio.Task[None]) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def drain_control_tasks(runner: Any) -> None:
    """Await the background tasks a ``play`` signal spawns (it's fire-and-forget
    off the receive path, so tests must let the nudges finish before asserting).
    """
    tasks = tuple(runner._control_tasks)
    if tasks:
        await asyncio.gather(*tasks)
