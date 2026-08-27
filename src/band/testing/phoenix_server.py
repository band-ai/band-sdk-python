"""In-process fake Phoenix Channels server for exercising BandLink's real
WebSocket stack end to end.

Speaks the real Phoenix Channels V2 wire protocol -- parsed and built with
phoenix_channels_python_client's own ``PHXProtocolHandler``/``make_message``,
never reimplemented -- behind a real loopback ``websockets.serve()`` socket.
Only the platform side of the wire is faked: ``BandLink`` -> ``WebSocketClient``
-> ``PHXChannelsClient`` all run unmocked, so real reconnect-supervisor
behavior, real close-code classification, and real join/leave acks are
actually exercised instead of assumed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from enum import StrEnum

from phoenix_channels_python_client.phx_messages import ChannelMessage, Event, PHXEvent
from phoenix_channels_python_client.protocol_handler import (
    PHXProtocolHandler,
    PhoenixChannelsProtocolVersion,
)
from phoenix_channels_python_client.utils import make_message
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class JoinOutcome(StrEnum):
    """What the fake server replies to one ``phx_join`` attempt with."""

    OK = "ok"
    REJECTED = "rejected"


class FakePhoenixServer:
    """A real loopback WebSocket server speaking Phoenix Channels V2.

    Construct only via :func:`fake_phoenix_server` -- never directly.
    """

    def __init__(self, *, join_outcomes: dict[str, Sequence[JoinOutcome]]) -> None:
        self._join_outcomes = {
            topic: list(outcomes) for topic, outcomes in join_outcomes.items()
        }
        self._protocol = PHXProtocolHandler(
            protocol_version=PhoenixChannelsProtocolVersion.V2
        )
        self.url: str = ""
        self.joined_topics: set[str] = set()
        self.received: list[ChannelMessage] = []
        self.connection_count = 0
        self._connection: ServerConnection | None = None
        self._connection_ready = asyncio.Event()
        # The client drops any message whose join_ref doesn't match the
        # ref it joined that topic with (protocol_handler.py's stale
        # join_ref guard), so a push() must echo the real one back.
        self._join_refs: dict[str, str | None] = {}

    def _next_join_outcome(self, topic: str) -> JoinOutcome:
        """Consume one outcome off ``topic``'s declared sequence, holding on
        the last entry once exhausted (unlisted topics always succeed)."""
        outcomes = self._join_outcomes.get(topic)
        if not outcomes:
            return JoinOutcome.OK
        outcome = outcomes[0]
        if len(outcomes) > 1:
            outcomes.pop(0)
        return outcome

    async def _reply(
        self,
        connection: ServerConnection,
        message: ChannelMessage,
        *,
        outcome: JoinOutcome,
    ) -> None:
        ok = outcome is JoinOutcome.OK
        payload = (
            {"status": "ok", "response": {}}
            if ok
            else {"status": "error", "response": {"reason": "rejected by fake server"}}
        )
        reply = make_message(
            topic=message.topic,
            event=PHXEvent.reply,
            ref=message.ref,
            join_ref=message.join_ref,
            payload=payload,
        )
        # PHXProtocolHandler.send_message is typed for ClientConnection, but
        # only calls .send() -- ServerConnection satisfies that identically.
        await self._protocol.send_message(connection, reply)  # pyrefly: ignore[bad-argument-type]

    async def _handle_message(
        self, connection: ServerConnection, message: ChannelMessage
    ) -> None:
        self.received.append(message)
        match message.event:
            case PHXEvent.join:
                outcome = self._next_join_outcome(message.topic)
                if outcome is JoinOutcome.OK:
                    self.joined_topics.add(message.topic)
                    self._join_refs[message.topic] = message.join_ref
                await self._reply(connection, message, outcome=outcome)
            case PHXEvent.leave:
                self.joined_topics.discard(message.topic)
                self._join_refs.pop(message.topic, None)
                await self._reply(connection, message, outcome=JoinOutcome.OK)
            case _ if message.topic == "phoenix":
                await self._reply(connection, message, outcome=JoinOutcome.OK)
            case _:
                logger.debug(
                    "Fake Phoenix server ignoring inbound event %s", message.event
                )

    async def _handler(self, connection: ServerConnection) -> None:
        self._connection = connection
        self.connection_count += 1
        self._connection_ready.set()
        try:
            async for raw in connection:
                message = self._protocol.parse_message(raw)
                await self._handle_message(connection, message)
        except ConnectionClosed:
            # Expected on abort_connection()/close_connection() and on a
            # genuine client-side drop -- not a fake-server failure.
            logger.debug("Connection closed")

    async def push(self, topic: str, event: str, payload: dict) -> None:
        """Inject an inbound event on an already-joined topic, as if the
        platform sent it unprompted."""
        await self._connection_ready.wait()
        assert self._connection is not None
        message = make_message(
            topic=topic,
            event=Event(event),
            payload=payload,
            join_ref=self._join_refs.get(topic),
        )
        await self._protocol.send_message(self._connection, message)  # pyrefly: ignore[bad-argument-type]

    async def close_connection(self, *, code: int = 1000, reason: str = "") -> None:
        """Graceful close with a specific close code -- e.g. 1000, which
        BandLink's reconnect policy treats as intentional and never
        reconnects from."""
        await self._connection_ready.wait()
        assert self._connection is not None
        await self._connection.close(code=code, reason=reason)

    async def abort_connection(self) -> None:
        """Sever the connection with no close handshake -- what a real
        network drop looks like to the client, so its own reconnect logic
        actually triggers (unlike a graceful close, which the client's
        policy reads as intentional and never reconnects from)."""
        await self._connection_ready.wait()
        assert self._connection is not None
        self._connection.transport.abort()


@asynccontextmanager
async def fake_phoenix_server(
    *, join_outcomes: dict[str, Sequence[JoinOutcome]] | None = None
) -> AsyncIterator[FakePhoenixServer]:
    """A real loopback Phoenix Channels server for exercising BandLink's real
    WebSocketClient/PHXChannelsClient stack, no mocks below BandLink.

    ``join_outcomes`` declares the whole join scenario up front, as data: a
    topic maps to the sequence of outcomes its successive join attempts get.
    Everything else about the scenario -- when the network drops, what
    events arrive -- is inherently a sequence of events in time, so it stays
    as explicit calls on the yielded server (``push``, ``close_connection``,
    ``abort_connection``).
    """
    server = FakePhoenixServer(join_outcomes=join_outcomes or {})
    async with serve(server._handler, "127.0.0.1", 0) as ws_server:
        bound_socket = next(iter(ws_server.sockets))
        port = bound_socket.getsockname()[1]
        server.url = f"ws://127.0.0.1:{port}"
        yield server
