"""A strict external Phoenix subscription boundary for raw capture tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from phoenix_channels_python_client.phx_messages import (
    ChannelMessage,
    Event,
    PHXMessage,
)

LEAVE_REJECTED = "Topic leave rejected"


class TopicPeer:
    def __init__(self) -> None:
        self.callbacks: dict[str, Callable[[ChannelMessage], Awaitable[None]]] = {}
        self.left: list[str] = []
        self.denied: str | None = None
        self.leave_failure: str | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def subscribe_to_topic(
        self, topic: str, callback: Callable[[ChannelMessage], Awaitable[None]]
    ) -> None:
        if not topic.startswith("room_activity:") or topic == self.denied:
            raise ValueError("Topic join rejected")
        if topic in self.callbacks:
            raise ValueError("Topic is already joined")
        self.entered.set()
        await self.release.wait()
        self.callbacks[topic] = callback

    async def unsubscribe_from_topic(self, topic: str) -> None:
        if topic == self.leave_failure:
            raise ValueError(LEAVE_REJECTED)
        del self.callbacks[topic]
        self.left.append(topic)

    async def emit(self, room_id: str, event: str, payload: dict[str, Any]) -> None:
        topic = f"room_activity:{room_id}"
        await self.callbacks[topic](
            PHXMessage(topic=topic, event=Event(event), payload=payload, ref=None)
        )
