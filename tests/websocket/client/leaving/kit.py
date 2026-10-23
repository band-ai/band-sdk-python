"""Observe server topic loss and gate real Phoenix leave replies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from band_sdk_core import chat_room_topic, room_participants_topic
from phoenix_channels_python_client.phx_messages import ChannelMessage, PHXEvent
from phoenix_channels_python_client.protocol_handler import (
    PhoenixChannelsProtocolVersion,
    PHXProtocolHandler,
)
from phoenix_channels_python_client.utils import make_message
from websockets.asyncio.server import ServerConnection

from band.platform.link import BandLink

ROOM_ID = "room-123"
ROOM_TOPIC = chat_room_topic(ROOM_ID)
PARTICIPANTS_TOPIC = room_participants_topic(ROOM_ID)


def make_link(server_url: str) -> BandLink:
    return BandLink(
        agent_id="agent-123",
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
    )


class TopicLoss(logging.Handler):
    """Signal the transport's completed topic-loss notification."""

    def __init__(self) -> None:
        super().__init__()
        self.completed = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == "Lost topic %s: %s":
            self.completed.set()


@contextmanager
def observe_topic_loss() -> Iterator[TopicLoss]:
    observer = TopicLoss()
    logger = logging.getLogger("phoenix_channels_python_client.client")
    logger.addHandler(observer)
    try:
        yield observer
    finally:
        logger.removeHandler(observer)
        observer.close()


@dataclass
class LeavePeer:
    """Acknowledge joins immediately and retain selected leave acknowledgements."""

    url: str = ""
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    accepted: bool = True
    protocol: PHXProtocolHandler = field(
        default_factory=lambda: PHXProtocolHandler(PhoenixChannelsProtocolVersion.V2)
    )

    async def reply(
        self, connection: ServerConnection, message: ChannelMessage, accepted: bool
    ) -> None:
        reply = make_message(
            topic=message.topic,
            event=PHXEvent.reply,
            ref=message.ref,
            join_ref=message.join_ref,
            payload={"status": "ok" if accepted else "error", "response": {}},
        )
        # The protocol helper only needs send(), shared by both socket sides.
        await self.protocol.send_message(connection, reply)  # pyrefly: ignore[bad-argument-type]

    async def handle(self, connection: ServerConnection) -> None:
        async for raw in connection:
            message = self.protocol.parse_message(raw)
            if message.event == PHXEvent.leave:
                self.requested.set()
                await self.released.wait()
                await self.reply(connection, message, self.accepted)
            else:
                await self.reply(connection, message, True)
