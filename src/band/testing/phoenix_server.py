"""In-process fake Phoenix Channels server for exercising BandLink's real
WebSocket stack end to end.

Speaks the real Phoenix Channels V2 wire protocol through the dependency's
codec behind a real loopback ``websockets.serve()`` socket.
Only the platform side of the wire is faked: ``BandLink`` -> ``WebSocketClient``
-> ``Client`` all run unmocked, so real reconnect-supervisor
behavior, real close-code classification, and real join/leave acks are
actually exercised instead of assumed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from urllib.parse import parse_qsl, urlsplit

from phoenix_channels_python_client import Message, ProtocolVersion, SystemEvent
from phoenix_channels_python_client.codec import decode_message, encode_message
from phoenix_channels_python_client.utils import make_message
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from band.client.streaming.errors import CONNECTION_CONFLICT_CODE

logger = logging.getLogger(__name__)


class JoinOutcome(StrEnum):
    """What the fake server replies to one ``phx_join`` attempt with."""

    OK = "ok"
    REJECTED = "rejected"


# The platform's refusal of `on_conflict=reject` when the agent already has a
# live connection (HTTP 409); request_id is null on a real upgrade.
CONNECTION_CONFLICT_MESSAGE = (
    "An active connection already exists for this agent. "
    "Set on_conflict=supersede or omit on_conflict to take over."
)
_CONFLICT_BODY = json.dumps(
    {
        "error": {
            "code": CONNECTION_CONFLICT_CODE,
            "message": CONNECTION_CONFLICT_MESSAGE,
            "request_id": None,
        }
    }
)


@dataclass(frozen=True)
class Refusal:
    """The HTTP response a refused upgrade gets."""

    status: HTTPStatus
    body: str
    headers: Mapping[str, str]


@dataclass
class HeldUpgrade:
    """One upgrade parked before the server answers it."""

    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    def release(self) -> None:
        self.released.set()


@dataclass(frozen=True)
class Upgrade:
    """One WebSocket upgrade request the server received, accepted or not."""

    query: dict[str, str]
    headers: Headers

    @classmethod
    def from_request(cls, request: Request) -> Upgrade:
        return cls(
            query=dict(parse_qsl(urlsplit(request.path).query)),
            headers=request.headers,
        )


@dataclass
class PhoenixConnection:
    """Observations belong to one accepted socket, including its rejoin pass."""

    socket: ServerConnection
    joined_topics: set[str] = field(default_factory=set)
    heartbeats: list[Message] = field(default_factory=list)
    # Phoenix discards pushes carrying a different join reference.
    join_refs: dict[str, str | None] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    async def wait_for_join(self, topic: str) -> None:
        while topic not in self.joined_topics:
            await self.changed.wait()

    async def wait_for_leave(self, topic: str) -> None:
        while topic in self.joined_topics:
            await self.changed.wait()

    async def wait_for_heartbeats(self, count: int) -> None:
        while len(self.heartbeats) < count:
            await self.changed.wait()


@dataclass
class DeferredJoin:
    """A selected join request retained without blocking heartbeat handling."""

    ready: asyncio.Event = field(default_factory=asyncio.Event)
    connection: PhoenixConnection | None = None
    message: Message | None = None
    outcome: JoinOutcome = JoinOutcome.OK


class FakePhoenixServer:
    """A real loopback WebSocket server speaking Phoenix Channels V2.

    Construct only via :func:`fake_phoenix_server` -- never directly.
    """

    def __init__(
        self,
        *,
        join_outcomes: dict[str, Sequence[JoinOutcome]],
        refuse_upgrades: bool,
        reply_heartbeats: bool,
    ) -> None:
        self._join_outcomes = {
            topic: list(outcomes) for topic, outcomes in join_outcomes.items()
        }
        self.reply_heartbeats = reply_heartbeats
        self.connections: list[PhoenixConnection] = []
        self._connections_changed = asyncio.Event()
        self._refusal: Refusal | None = (
            Refusal(HTTPStatus.CONFLICT, _CONFLICT_BODY, {})
            if refuse_upgrades
            else None
        )
        self._held_upgrade: HeldUpgrade | None = None
        self._deferred_joins: dict[str, DeferredJoin] = {}
        self.url: str = ""
        self.joined_topics: set[str] = set()
        self.received: list[Message] = []
        # Includes refused upgrades; connection_count counts accepted ones only.
        self.upgrades: list[Upgrade] = []
        self.connection_count = 0
        self._connection: ServerConnection | None = None
        self._connection_ready = asyncio.Event()

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

    async def _process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        """Answer a refused upgrade with an HTTP response before the handshake
        completes; ``None`` lets it proceed."""
        self.upgrades.append(Upgrade.from_request(request))
        held, self._held_upgrade = self._held_upgrade, None
        if held is not None:
            held.arrived.set()
            await held.released.wait()
        if (refusal := self._refusal) is not None:
            response = connection.respond(refusal.status, refusal.body)
            response.headers.update(refusal.headers)
            return response
        return None

    def _forget_topic(self, observed: PhoenixConnection, topic: str) -> None:
        """Drop ``topic`` from joined-membership bookkeeping -- shared by a
        leave and a rejected join, since both mean the server no longer
        considers the topic joined."""
        self.joined_topics.discard(topic)
        observed.joined_topics.discard(topic)
        observed.join_refs.pop(topic, None)
        observed.notify()

    async def _reply(
        self,
        connection: ServerConnection,
        message: Message,
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
            event=SystemEvent.reply,
            ref=message.ref,
            join_ref=message.join_ref,
            payload=payload,
        )
        await connection.send(encode_message(reply, ProtocolVersion.V2))

    async def _complete_join(
        self, observed: PhoenixConnection, message: Message, outcome: JoinOutcome
    ) -> None:
        await self._reply(observed.socket, message, outcome=outcome)
        if outcome is JoinOutcome.OK:
            self.joined_topics.add(message.topic)
            observed.joined_topics.add(message.topic)
            observed.join_refs[message.topic] = message.join_ref
        else:
            self._forget_topic(observed, message.topic)
        observed.notify()

    async def _handle_message(
        self, observed: PhoenixConnection, message: Message
    ) -> None:
        self.received.append(message)
        match message.event:
            case SystemEvent.join:
                outcome = self._next_join_outcome(message.topic)
                deferred = self._deferred_joins.pop(message.topic, None)
                if deferred is not None:
                    deferred.connection = observed
                    deferred.message = message
                    deferred.outcome = outcome
                    deferred.ready.set()
                else:
                    await self._complete_join(observed, message, outcome)
            case SystemEvent.leave:
                joined = message.topic in observed.joined_topics
                join_ref = observed.join_refs.get(message.topic)
                self._forget_topic(observed, message.topic)
                await self._reply(observed.socket, message, outcome=JoinOutcome.OK)
                if joined:
                    close = make_message(
                        topic=message.topic,
                        event=SystemEvent.close,
                        ref=join_ref,
                        join_ref=join_ref,
                    )
                    await observed.socket.send(
                        encode_message(close, ProtocolVersion.V2)
                    )
            case _ if message.topic == "phoenix":
                if self.reply_heartbeats:
                    await self._reply(observed.socket, message, outcome=JoinOutcome.OK)
                observed.heartbeats.append(message)
                observed.notify()
            case _:
                logger.debug(
                    "Fake Phoenix server ignoring inbound event %s", message.event
                )

    async def _handler(self, connection: ServerConnection) -> None:
        self._connection = connection
        self.connection_count += 1
        observed = PhoenixConnection(connection)
        self.connections.append(observed)
        self._connections_changed.set()
        self._connections_changed = asyncio.Event()
        self._connection_ready.set()
        try:
            async for raw in connection:
                message = decode_message(raw, ProtocolVersion.V2).message
                await self._handle_message(observed, message)
        except ConnectionClosed:
            logger.debug("Connection closed")

    async def wait_for_connection(self, number: int) -> PhoenixConnection:
        """Wait for a particular accepted connection (one-based)."""
        if number < 1:
            raise ValueError("Connection number must be positive")
        while len(self.connections) < number:
            await self._connections_changed.wait()
        return self.connections[number - 1]

    @contextmanager
    def refusing_upgrades(
        self,
        status: HTTPStatus,
        *,
        body: str = "Upgrade refused",
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[None]:
        """Temporarily refuse every upgrade with this response."""
        previous = self._refusal
        self._refusal = Refusal(status, body, headers or {})
        try:
            yield
        finally:
            self._refusal = previous

    @contextmanager
    def holding_upgrades(self) -> Iterator[HeldUpgrade]:
        """Park the next upgrade until released (at the latest on exit).

        Release it before the client's handshake ``open_timeout`` elapses, or
        the attempt fails as a timed-out connect.
        """
        if self._held_upgrade is not None:
            raise ValueError("An upgrade is already held")
        held = self._held_upgrade = HeldUpgrade()
        try:
            yield held
        finally:
            if self._held_upgrade is held:
                self._held_upgrade = None
            held.release()

    @contextmanager
    def defer_join(self, topic: str) -> Iterator[DeferredJoin]:
        """Capture the next selected join; all other frames keep flowing."""
        if topic in self._deferred_joins:
            raise ValueError("A join is already deferred for this topic")
        deferred = DeferredJoin()
        self._deferred_joins[topic] = deferred
        try:
            yield deferred
        finally:
            if self._deferred_joins.get(topic) is deferred:
                del self._deferred_joins[topic]

    async def release_join(self, deferred: DeferredJoin) -> None:
        """Reply on the captured socket/reference, never on its replacement."""
        await deferred.ready.wait()
        assert deferred.connection is not None and deferred.message is not None
        message = deferred.message
        deferred.message = None
        await self._complete_join(deferred.connection, message, deferred.outcome)

    async def push(self, topic: str, event: str, payload: dict) -> None:
        """Inject an inbound event on an already-joined topic, as if the
        platform sent it unprompted."""
        await self._connection_ready.wait()
        assert self._connection is not None
        message = make_message(
            topic=topic,
            event=event,
            payload=payload,
            join_ref=self.connections[-1].join_refs.get(topic),
        )
        await self._connection.send(encode_message(message, ProtocolVersion.V2))

    async def crash_channel(self, topic: str) -> None:
        """Crash one joined channel (``phx_error``); the socket stays up."""
        await self.push(topic, SystemEvent.error, {})

    async def close_connection(self, *, code: int = 1000, reason: str = "") -> None:
        """Server-initiated close handshake with a specific close code."""
        await self._connection_ready.wait()
        assert self._connection is not None
        await self._connection.close(code=code, reason=reason)

    @contextmanager
    def stalled(self) -> Iterator[None]:
        """Stop reading from the client, as a slow peer does: whatever the
        client sends meanwhile, close handshake included, waits unanswered."""
        assert self._connection is not None
        transport = self._connection.transport
        transport.pause_reading()
        try:
            yield
        finally:
            transport.resume_reading()

    async def abort_connection(self) -> None:
        """Sever the connection with no close handshake -- what a real
        network drop looks like to the client."""
        await self._connection_ready.wait()
        assert self._connection is not None
        self._connection.transport.abort()


@asynccontextmanager
async def fake_phoenix_server(
    *,
    join_outcomes: dict[str, Sequence[JoinOutcome]] | None = None,
    refuse_upgrades: bool = False,
    reply_heartbeats: bool = True,
) -> AsyncIterator[FakePhoenixServer]:
    """A real loopback Phoenix Channels server for exercising BandLink's real
    WebSocketClient/Client stack, no mocks below BandLink.

    ``reply_heartbeats`` defaults to automatic replies; disabling it preserves
    frame reading and WebSocket Ping/Pong. Connection-specific observations
    identify successful replacement joins rather than historical membership.

    ``join_outcomes`` declares the whole join scenario up front, as data: a
    topic maps to the sequence of outcomes its successive join attempts get.
    ``refuse_upgrades`` answers every upgrade with the platform's 409
    ``connection_conflict``. Everything else about the scenario --
    when the network drops, what events arrive -- is inherently a sequence of
    events in time, so it stays as explicit calls on the yielded server
    (``push``, ``close_connection``, ``abort_connection``).
    """
    server = FakePhoenixServer(
        join_outcomes=join_outcomes or {},
        refuse_upgrades=refuse_upgrades,
        reply_heartbeats=reply_heartbeats,
    )
    async with serve(
        server._handler,
        "127.0.0.1",
        0,
        process_request=server._process_request,
    ) as ws_server:
        bound_socket = next(iter(ws_server.sockets))
        port = bound_socket.getsockname()[1]
        server.url = f"ws://127.0.0.1:{port}"
        yield server
