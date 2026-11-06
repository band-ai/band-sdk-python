"""A strict external Phoenix subscription boundary for raw capture tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Self

from phoenix_channels_python_client.messages import (
    Message,
    UserMessage,
)

LEAVE_REJECTED = "Topic leave rejected"


class TopicChannel:
    """A capture subscription handle backed by the strict topic peer."""

    def __init__(self, peer: TopicPeer, topic: str) -> None:
        self.peer = peer
        self.topic = topic
        self.handler: Callable[[Message], Awaitable[None]] | None = None

    def on_any(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        self.handler = handler

    async def __aenter__(self) -> Self:
        if self.handler is None:
            raise ValueError("Topic requires a message handler")
        await self.peer.join_topic(self.topic, self.handler)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.peer.leave_topic(self.topic)


class TopicPeer:
    def __init__(self) -> None:
        self.callbacks: dict[str, Callable[[Message], Awaitable[None]]] = {}
        self.left: list[str] = []
        self.denied: str | None = None
        self.leave_failure: str | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def channel(self, topic: str) -> TopicChannel:
        return TopicChannel(self, topic)

    async def join_topic(
        self, topic: str, callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        if not topic.startswith("room_activity:") or topic == self.denied:
            raise ValueError("Topic join rejected")
        if topic in self.callbacks:
            raise ValueError("Topic is already joined")
        self.entered.set()
        await self.release.wait()
        self.callbacks[topic] = callback

    async def leave_topic(self, topic: str) -> None:
        if topic == self.leave_failure:
            raise ValueError(LEAVE_REJECTED)
        del self.callbacks[topic]
        self.left.append(topic)

    async def emit(self, room_id: str, event: str, payload: dict[str, Any]) -> None:
        topic = f"room_activity:{room_id}"
        await self.callbacks[topic](
            UserMessage(topic=topic, event=event, payload=payload, ref=None)
        )
