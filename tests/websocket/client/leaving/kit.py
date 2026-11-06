"""Observe server topic loss and gate real Phoenix leave replies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from band_sdk_core import chat_room_topic, room_participants_topic
from phoenix_channels_python_client import Message, ProtocolVersion, SystemEvent
from phoenix_channels_python_client.codec import decode_message, encode_message
from phoenix_channels_python_client.messages import HEARTBEAT_EVENT, PHOENIX_TOPIC
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
        if record.getMessage().startswith("Lost topic "):
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
    close_requested: asyncio.Event = field(default_factory=asyncio.Event)
    close_released: asyncio.Event = field(default_factory=asyncio.Event)
    leaves: asyncio.Queue[tuple[ServerConnection, Message]] = field(
        default_factory=asyncio.Queue
    )

    async def end_channel(self, connection: ServerConnection, message: Message) -> None:
        close = make_message(
            topic=message.topic,
            event=SystemEvent.close,
            ref=message.join_ref,
            join_ref=message.join_ref,
        )
        await connection.send(encode_message(close, ProtocolVersion.V2))

    async def reply(
        self, connection: ServerConnection, message: Message, accepted: bool
    ) -> None:
        reply = make_message(
            topic=message.topic,
            event=SystemEvent.reply,
            ref=message.ref,
            join_ref=message.join_ref,
            payload={"status": "ok" if accepted else "error", "response": {}},
        )
        await connection.send(encode_message(reply, ProtocolVersion.V2))

    async def handle(self, connection: ServerConnection) -> None:
        async for raw in connection:
            message = decode_message(raw, ProtocolVersion.V2).message
            if message.event == SystemEvent.leave:
                self.leaves.put_nowait((connection, message))
                self.requested.set()
                await self.released.wait()
                await self.reply(connection, message, self.accepted)
                if self.accepted:
                    self.close_requested.set()
                    await self.close_released.wait()
                    await self.end_channel(connection, message)
            elif message.event == SystemEvent.join or (
                message.topic == PHOENIX_TOPIC and message.event == HEARTBEAT_EVENT
            ):
                await self.reply(connection, message, True)
            else:
                raise ValueError(f"Unsupported peer event {message.event}")
