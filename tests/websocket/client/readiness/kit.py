"""Kit: an ordered timeline of room readiness and delivery, and gap sources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from band.client.streaming import MessageCreatedPayload, RoomReadiness, WebSocketClient
from band.client.streaming.client import WireEvent
from band.testing import FakePhoenixServer, JoinOutcome, fake_phoenix_server
from band.testing.phoenix_server import DeferredJoin
from tests.platform.recovery.kit import ROOM_ID, ROOM_TOPIC
from tests.sessionpolicies import millisecond_backoff
from tests.websocket.client.kit import VALID_MESSAGE_CREATED_PAYLOAD


@dataclass(frozen=True)
class Delivered:
    message_id: str


@dataclass(frozen=True)
class Started:
    """A held message callback began and is waiting for release."""

    message_id: str


@dataclass(frozen=True)
class Cancelled:
    message_id: str


Entry = RoomReadiness | Delivered | Started | Cancelled


class Timeline:
    """One consumer's ordered view of a room: readiness and deliveries.

    The socket-level ``on_reconnect`` is unordered relative to room
    readiness, so it is observed separately.
    """

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.held: dict[str, asyncio.Event] = {}
        self.socket_reconnected = asyncio.Event()
        self._changed = asyncio.Event()

    def record(self, entry: Entry) -> None:
        self.entries.append(entry)
        self._changed.set()
        self._changed = asyncio.Event()

    def ready(self, readiness: RoomReadiness) -> None:
        self.record(readiness)

    async def deliver(self, payload: MessageCreatedPayload) -> None:
        if (release := self.held.get(payload.id)) is not None:
            self.record(Started(payload.id))
            try:
                await release.wait()
            except asyncio.CancelledError:
                self.record(Cancelled(payload.id))
                raise
        self.record(Delivered(payload.id))

    async def reconnected(self) -> None:
        self.socket_reconnected.set()

    def hold(self, message_id: str) -> asyncio.Event:
        """Park ``message_id``'s callback until the returned event is set."""
        release = self.held[message_id] = asyncio.Event()
        return release

    async def wait_for(self, entry: Entry) -> None:
        while entry not in self.entries:
            await self._changed.wait()


def readiness(generation: int, room_id: str = ROOM_ID) -> RoomReadiness:
    return RoomReadiness(room_id=room_id, generation=generation)


def message(message_id: str) -> dict:
    return {**VALID_MESSAGE_CREATED_PAYLOAD, "id": message_id}


async def push_message(server: FakePhoenixServer, message_id: str) -> None:
    await server.push(ROOM_TOPIC, WireEvent.MESSAGE_CREATED, message(message_id))


async def crash_room_channel(server: FakePhoenixServer) -> None:
    """The platform crashes the room channel; the socket stays up."""
    await server.crash_channel(ROOM_TOPIC)


async def drop_socket(server: FakePhoenixServer) -> None:
    await server.abort_connection()


GapSource = Callable[[FakePhoenixServer], Awaitable[None]]


async def answer_join_then_push(
    server: FakePhoenixServer, deferred: DeferredJoin, message_id: str
) -> None:
    """Confirm a held join and push a message right behind its reply."""
    await deferred.ready.wait()
    await server.release_join(deferred)
    await push_message(server, message_id)


async def join_room(
    client: WebSocketClient, timeline: Timeline, *, room_id: str = ROOM_ID
) -> None:
    await client.join_chat_room_channel(
        room_id, timeline.deliver, on_ready=timeline.ready
    )


@asynccontextmanager
async def readiness_session(
    timeline: Timeline,
    *,
    join_outcomes: dict[str, Sequence[JoinOutcome]] | None = None,
) -> AsyncIterator[tuple[FakePhoenixServer, WebSocketClient]]:
    """A connected client that reports its reconnects to ``timeline``."""
    async with (
        fake_phoenix_server(join_outcomes=join_outcomes) as server,
        WebSocketClient(
            server.url,
            "test-key",
            session_policy=millisecond_backoff(),
            on_reconnect=timeline.reconnected,
        ) as client,
    ):
        yield server, client
