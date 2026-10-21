"""A REST peer serving one room's deliveries, and a started room execution over it.

The peer speaks the platform's wire format to the real generated client, so
what a room learns about a delivery (notably ``metadata.delivery_status``)
arrives exactly as the client surfaces it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import httpx
from band_sdk_core import ClaimRegistry

from band.client.streaming import DeliveryStatus, MessageCreatedPayload, MessageMetadata
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext, ExecutionHandler
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.platform.settlement.kit import NOT_FOUND, Reply, link_over
from tests.runtime.batching.kit import ControlScript, Turns
from tests.runtime.conftest import Waiting
from tests.runtime.helpers import (
    AGENT_ID,
    EPOCH,
    ROOM_ID,
    SENDER_ID,
    STOP_SECONDS,
    rest_client_over,
)

PlatformStopHook = Callable[[ExecutionContext, TurnScope], Awaitable[None]]

_WRITE = re.compile(
    r"/messages/(?P<message_id>[^/]+)/(?P<action>processing|processed|failed)$"
)
_ATTEMPT_OUTCOME = {
    DeliveryStatus.PROCESSING: "processing",
    DeliveryStatus.PROCESSED: "success",
    DeliveryStatus.FAILED: "failed",
}


def read(cursor: str | None) -> str:
    """Journal entry: the room read one page of the actionable list."""
    return f"read after {cursor}" if cursor else "read from start"


def turn(*message_ids: str) -> str:
    """Journal entry: the host ran one turn over these originals."""
    return "turn " + "+".join(message_ids)


def control(message_id: str) -> str:
    """Journal entry: the host consumed this message as a control."""
    return f"control {message_id}"


def write(action: DeliveryStatus, message_id: str, *, confirmed: bool = True) -> str:
    """Journal entry: a lifecycle write reached the platform, confirmed or not."""
    return f"{action} {message_id}" + ("" if confirmed else " (unconfirmed)")


def error_causes(
    records: Iterable[logging.LogRecord],
) -> set[type[BaseException] | None]:
    """The exception type behind each record logged at ERROR or above."""
    return {
        record.exc_info[0] if record.exc_info else None
        for record in records
        if record.levelno >= logging.ERROR
    }


def delivery_entry(status: DeliveryStatus) -> dict[str, Any]:
    """One recipient's ``delivery_status`` value, shaped as the platform sends it."""
    stamp = EPOCH.isoformat()
    attempt: dict[str, Any] = {
        "attempt_number": 1,
        "status": _ATTEMPT_OUTCOME.get(status, status),
    }
    if status is DeliveryStatus.FAILED:
        attempt["error"] = "the model failed"
    entry: dict[str, Any] = {
        "status": status,
        "current_attempt": 1,
        "delivered_at": stamp,
        "attempts": [attempt],
    }
    if status is DeliveryStatus.PROCESSED:
        entry["processed_at"] = stamp
    return entry


@dataclass
class Row:
    """One stored message and every recipient's delivery status."""

    message_id: str
    inserted_at: datetime
    statuses: dict[str, DeliveryStatus]
    # The list and /next omit some deliveries the WebSocket still carries.
    listed: bool = True
    sender_id: str = SENDER_ID
    sender_type: str = "User"

    @property
    def key(self) -> tuple[datetime, str]:
        return self.inserted_at, self.message_id

    @property
    def outstanding(self) -> bool:
        return (
            self.listed and self.statuses.get(AGENT_ID) is not DeliveryStatus.PROCESSED
        )

    @property
    def wire(self) -> dict[str, Any]:
        stamp = self.inserted_at.isoformat()
        metadata: dict[str, Any] = {
            "mentions": [{"id": AGENT_ID, "name": "Agent"}],
            "status": "sent",
        }
        if self.statuses:
            metadata["delivery_status"] = {
                recipient: delivery_entry(status)
                for recipient, status in self.statuses.items()
            }
        return {
            "id": self.message_id,
            "chat_room_id": ROOM_ID,
            "content": self.message_id,
            "sender_id": self.sender_id,
            "sender_type": self.sender_type,
            "sender_name": "User",
            "message_type": "text",
            "metadata": metadata,
            "inserted_at": stamp,
            "updated_at": stamp,
        }


@dataclass(frozen=True)
class Read:
    cursor: str | None
    at: float


@dataclass(frozen=True)
class Write:
    message_id: str
    action: DeliveryStatus
    at: float
    confirmed: bool


def _replying(reply: Reply) -> httpx.Response:
    return httpx.Response(
        reply.status, content=reply.body, headers={"content-type": "application/json"}
    )


class RunawayRescan(Exception):
    """Raised by the peer when a room reads without end instead of waiting."""


class RestPeer:
    """The platform side of one room, spoken over HTTP to the real client.

    Rows are listed oldest first by ``(inserted_at, id)``. Only ``processed``
    leaves the actionable list: ``processing`` and ``failed`` rows stay, each
    carrying its ``delivery_status`` per recipient.
    """

    def __init__(self, *, runaway_limit: int = 100) -> None:
        self.rows: dict[str, Row] = {}
        self.reads: list[Read] = []
        self.nexts: list[float] = []
        self.writes: list[Write] = []
        self.events_posted = 0
        self.journal: list[str] = []
        self.refuse_processing: set[str] = set()
        self.deleted: set[str] = set()
        # A non-participant gets 404 not_found on every room route, as a deleted message does.
        self.participant = True
        self.confirm_terminal = True
        # Answers every terminal write verbatim, confirming none of them.
        self.terminal_error: Reply | None = None
        self.runaway_limit = runaway_limit

    def post(
        self,
        message_id: str,
        *,
        status: DeliveryStatus | None = None,
        at: float | None = None,
        others: dict[str, DeliveryStatus] | None = None,
        listed: bool = True,
        sender_id: str = SENDER_ID,
        sender_type: str = "User",
    ) -> MessageEvent:
        """Store a message ``at`` seconds after the epoch; return its WebSocket event."""
        moment = EPOCH + timedelta(seconds=len(self.rows) if at is None else at)
        statuses = dict(others or {})
        if status is not None:
            statuses[AGENT_ID] = status
        self.rows[message_id] = Row(
            message_id, moment, statuses, listed, sender_id, sender_type
        )
        return make_message_event(
            room_id=ROOM_ID,
            msg_id=message_id,
            content=message_id,
            sender_id=sender_id,
            sender_type=sender_type,
            inserted_at=moment.isoformat(),
        )

    def row(self, message_id: str) -> PlatformMessage:
        """A stored message as the actionable list returns it."""
        row = self.rows[message_id]
        return PlatformMessage(
            id=message_id,
            room_id=ROOM_ID,
            content=message_id,
            sender_id=row.sender_id,
            sender_type=row.sender_type,
            sender_name="User",
            message_type="text",
            metadata=row.wire["metadata"],
            created_at=row.inserted_at,
        )

    def replay(self, message_id: str) -> MessageEvent:
        """A WebSocket redelivery of a stored message, carrying its current statuses."""
        row = self.rows[message_id]
        return make_message_event(
            room_id=ROOM_ID,
            msg_id=message_id,
            content=message_id,
            sender_id=row.sender_id,
            sender_type=row.sender_type,
            inserted_at=row.inserted_at.isoformat(),
            metadata=MessageMetadata(
                mentions=[],
                delivery_status={
                    recipient: delivery_entry(status)
                    for recipient, status in row.statuses.items()
                },
            ),
        )

    def delete(self, message_id: str) -> None:
        """Soft-delete a message: it leaves the list and every write on it is not found."""
        self.rows[message_id].listed = False
        self.deleted.add(message_id)

    def set_status(self, message_id: str, status: DeliveryStatus | None) -> None:
        """Change this agent's status as another runtime would, outside the room."""
        statuses = self.rows[message_id].statuses
        if status is None:
            statuses.pop(AGENT_ID, None)
        else:
            statuses[AGENT_ID] = status

    def status_of(self, message_id: str) -> DeliveryStatus | None:
        return self.rows[message_id].statuses.get(AGENT_ID)

    def writes_of(
        self, action: DeliveryStatus, message_id: str | None = None
    ) -> list[Write]:
        return [
            write
            for write in self.writes
            if write.action is action and message_id in (None, write.message_id)
        ]

    def write_times(self, action: DeliveryStatus) -> list[float]:
        return [write.at for write in self.writes_of(action)]

    def without_reads(self) -> list[str]:
        """The journal's turns, controls and lifecycle writes, in order."""
        return [entry for entry in self.journal if not entry.startswith("read")]

    @property
    def polls(self) -> int:
        """Every read of the platform's outstanding work, list or /next."""
        return len(self.reads) + len(self.nexts)

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/messages"):
            return self._list(request)
        if request.method == "GET" and path.endswith("/messages/next"):
            return self._next()
        if request.method == "POST" and (write := _WRITE.search(path)):
            return self._write(write["message_id"], DeliveryStatus(write["action"]))
        if request.method == "POST" and path.endswith("/events"):
            self.events_posted += 1
            return httpx.Response(
                200,
                json={
                    "data": {"id": "event-1", "message_type": "error", "success": True}
                },
            )
        return httpx.Response(
            200, json={"data": [], "metadata": {"has_more": False, "limit": 50}}
        )

    def _outstanding(self) -> list[Row]:
        return sorted(
            (row for row in self.rows.values() if row.outstanding),
            key=lambda row: row.key,
        )

    def _guard_against_runaway(self) -> None:
        """Fail a room that polls without end, so it cannot hang the test."""
        if self.polls >= self.runaway_limit:
            raise RunawayRescan

    def _list(self, request: httpx.Request) -> httpx.Response:
        self._guard_against_runaway()
        cursor = request.url.params.get("cursor")
        limit = int(request.url.params["limit"])
        if not self.participant:
            return _replying(NOT_FOUND)
        rows = self._outstanding()
        if cursor is not None:
            rows = [row for row in rows if row.key > self.rows[cursor].key]
        page, more = rows[:limit], rows[limit:]
        self.reads.append(Read(cursor, asyncio.get_running_loop().time()))
        self.journal.append(read(cursor))
        return httpx.Response(
            200,
            json={
                "data": [row.wire for row in page],
                "metadata": {
                    "limit": limit,
                    "has_more": bool(more),
                    "next_cursor": page[-1].message_id if more else None,
                },
            },
        )

    def _next(self) -> httpx.Response:
        self._guard_against_runaway()
        self.nexts.append(asyncio.get_running_loop().time())
        if not self.participant:
            return _replying(NOT_FOUND)
        rows = self._outstanding()
        if not rows:
            return httpx.Response(204)
        return httpx.Response(200, json={"data": rows[0].wire})

    def _write(self, message_id: str, action: DeliveryStatus) -> httpx.Response:
        terminal = action is not DeliveryStatus.PROCESSING
        if message_id in self.deleted or not self.participant:
            failure: Reply | None = NOT_FOUND
        else:
            failure = self.terminal_error if terminal else None
        confirmed = failure is None and (
            self.confirm_terminal
            if terminal
            else message_id not in self.refuse_processing
        )
        self.writes.append(
            Write(message_id, action, asyncio.get_running_loop().time(), confirmed)
        )
        self.journal.append(write(action, message_id, confirmed=confirmed))
        if failure is not None:
            return _replying(failure)
        if confirmed:
            self.rows[message_id].statuses[AGENT_ID] = action
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": message_id,
                    "attempt_number": 1,
                    "status": action,
                    "success": confirmed,
                }
            },
        )


@dataclass
class JournaledTurns(Turns):
    """The batching kit's host, also noting each turn in the shared journal."""

    journal: list[str] = field(default_factory=list)

    async def record(self, ids: tuple[str, ...]) -> None:
        if self.accepting:
            self.journal.append(turn(*ids))
        await super().record(ids)


@dataclass
class JournaledControls(ControlScript):
    """The batching kit's control vocabulary, noting each consumed control."""

    journal: list[str] = field(default_factory=list)
    payloads: list[MessageCreatedPayload] = field(default_factory=list)

    async def handle(self, ctx: ExecutionContext, event: MessageEvent) -> bool:
        consumed = await super().handle(ctx, event)
        if consumed:
            assert event.payload is not None
            self.payloads.append(event.payload)
            self.journal.append(control(event.payload.id))
        return consumed


def session_config(*, batching: bool, retries: int = 1) -> SessionConfig:
    return SessionConfig(
        max_message_retries=retries,
        enable_context_hydration=False,
        enable_working_state=False,
    )


@dataclass
class Observer(Waiting):
    """What a test can wait for and read back from a room over the peer."""

    peer: RestPeer
    turns: JournaledTurns

    async def reached(self, status: DeliveryStatus, *message_ids: str) -> None:
        await self.until(
            lambda: all(self.peer.status_of(m) is status for m in message_ids)
        )

    async def settled(self, *message_ids: str) -> None:
        await self.reached(DeliveryStatus.PROCESSED, *message_ids)

    @property
    def admission_attempts(self) -> list[str]:
        """Every message the room asked the platform to mark processing."""
        return [w.message_id for w in self.peer.writes_of(DeliveryStatus.PROCESSING)]

    @property
    def progress(self) -> tuple[list[tuple[str, ...]], list[str]]:
        """The turns run and the messages admitted so far."""
        return list(self.turns.ids), self.admission_attempts


@dataclass
class Session(Observer):
    """One started room execution."""

    ctx: ExecutionContext
    started_at: float
    controls: JournaledControls

    async def deliver(self, *events: PlatformEvent) -> None:
        for event in events:
            await self.ctx.on_event(event)

    async def resync(self) -> None:
        await self.ctx.request_resync()
        await self.quiesce()

    def since_start(self, instants: list[float]) -> list[float]:
        return [instant - self.started_at for instant in instants]


class Ending(Enum):
    """How a gated turn is brought to its end."""

    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPT = "interrupt"

    @property
    def terminal(self) -> DeliveryStatus:
        return (
            DeliveryStatus.FAILED
            if self is Ending.FAILURE
            else DeliveryStatus.PROCESSED
        )

    @property
    def fail_on(self) -> frozenset[str]:
        """The turns the host fails for this ending, given the turn ``m1``."""
        return frozenset({"m1"}) if self is Ending.FAILURE else frozenset()

    def end(self, session: Session) -> None:
        if self is Ending.INTERRUPT:
            assert session.ctx.interrupt()
        else:
            session.turns.release.set()


@contextlib.asynccontextmanager
async def started_session(
    peer: RestPeer,
    *,
    batching: bool = False,
    retries: int = 1,
    gate: bool = False,
    accepting: bool = True,
    fail_on: frozenset[str] = frozenset(),
    controls: frozenset[str] = frozenset(),
    claims: ClaimRegistry | None = None,
    on_platform_stop: PlatformStopHook | None = None,
) -> AsyncIterator[Session]:
    """A room execution started over the peer through the real client."""
    turns = JournaledTurns(
        gate=gate, accepting=accepting, fail_on=set(fail_on), journal=peer.journal
    )
    scripted = JournaledControls(recognized=set(controls), journal=peer.journal)
    async with rest_client_over(peer.answer) as rest:
        ctx = ExecutionContext(
            ROOM_ID,
            link_over(rest),
            turns.single,
            config=session_config(batching=batching, retries=retries),
            agent_id=AGENT_ID,
            claim_registry=claims,
            on_execute_batch=turns.batch,
            on_message_control=scripted.handle if controls else None,
            is_message_control=scripted.recognizes if controls else None,
            on_platform_stop=on_platform_stop,
        )
        started_at = asyncio.get_running_loop().time()
        await ctx.start()
        try:
            yield Session(peer, turns, ctx, started_at, scripted)
        finally:
            turns.release.set()
            await ctx.stop(timeout=STOP_SECONDS)


@contextlib.asynccontextmanager
async def room_over(
    peer: RestPeer,
    on_execute: ExecutionHandler,
    *,
    claims: ClaimRegistry | None = None,
    retries: int = 1,
) -> AsyncIterator[ExecutionContext]:
    """An unstarted room execution over the peer, stopped on exit."""
    async with rest_client_over(peer.answer) as rest:
        ctx = ExecutionContext(
            ROOM_ID,
            link_over(rest),
            on_execute,
            config=session_config(batching=False, retries=retries),
            agent_id=AGENT_ID,
            claim_registry=claims,
        )
        try:
            yield ctx
        finally:
            await ctx.stop(timeout=STOP_SECONDS)
