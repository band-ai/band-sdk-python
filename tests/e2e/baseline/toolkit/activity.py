"""Opt-in test observation of ephemeral room activity, independent of SDK receiving."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from phoenix_channels_python_client.phx_messages import PHXMessage

from band.client.streaming import WebSocketClient
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient


class ActivityEvent(StrEnum):
    STARTED = "agent_activity_started"
    CHANGED = "agent_activity_changed"
    STOPPED = "agent_activity_stopped"


def activity_topic(room_id: str) -> str:
    return f"room_activity:{room_id}"


@dataclass(frozen=True)
class ActivityFrame:
    event: ActivityEvent
    payload: dict[str, Any]

    @property
    def agent_id(self) -> str:
        return str(self.payload.get("agent_id", ""))

    @property
    def detail(self) -> dict[str, Any]:
        detail = self.payload.get("activity")
        return detail if isinstance(detail, dict) else {}


class Activities(list[ActivityFrame]):
    def has_tool(self, agent_id: str, names: Collection[str]) -> bool:
        return any(
            frame.agent_id == agent_id
            and frame.detail.get("kind") == "tool_call"
            and frame.detail.get("name") in names
            for frame in self
        )

    def snapshot(self) -> int:
        return len(self)

    def has_stopped(self, agent_id: str, *, since: int = 0) -> bool:
        return any(
            frame.agent_id == agent_id and frame.event == ActivityEvent.STOPPED
            for frame in self[since:]
        )

    def assert_names_only(self, *private_values: str) -> None:
        for frame in self:
            assert set(frame.detail) <= {"kind", "name"}, frame.detail
        serialized = json.dumps([frame.payload for frame in self])
        for value in private_values:
            assert value not in serialized, (
                "Private tool data appeared in room activity"
            )


@asynccontextmanager
async def activity_subscription(
    ws: WebSocketClient | TrackingWebSocketClient,
    room_id: str,
    handler: Callable[[PHXMessage], Awaitable[None]],
) -> AsyncIterator[None]:
    raw = ws.ws if isinstance(ws, TrackingWebSocketClient) else ws
    client = raw.client
    if client is None:
        raise RuntimeError("Activity observation requires a connected WebSocket")
    topic = activity_topic(room_id)
    try:
        await client.subscribe_to_topic(topic, handler)
        yield
    finally:
        if topic in raw.joined_topics():
            await client.unsubscribe_from_topic(topic)
