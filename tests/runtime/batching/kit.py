"""A one-room platform and a started room execution for batching scenarios."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from band.client.streaming import DeliveryStatus
from band.core.exceptions import InputNotAccepted
from band.core.settlement import SettlementResult
from band.platform.event import MessageEvent
from band.platform.message_lifecycle import ActionablePage
from band.runtime.execution import ExecutionContext
from band.runtime.types import PlatformMessage, SessionConfig
from tests.conftest import make_message_event, make_participant_mock

AGENT_ID = "agent-1"
ROOM_ID = "room-1"
EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
WAIT_SECONDS = 5.0


def room_context(*, stored: list[dict[str, Any]] | None = None) -> MagicMock:
    """An uninitialized room context with three known participants and
    ``stored`` as the room's persisted messages."""
    context = MagicMock()
    context.room_id = ROOM_ID
    context.is_llm_initialized = False
    context.config = SessionConfig(consume_all_queued=True)
    context.participants = [
        {"id": "alice", "name": "Alice", "type": "User"},
        {"id": "bob", "name": "Bob", "type": "User"},
        {"id": "helper", "name": "Helper", "type": "Agent"},
    ]
    context.peek_pending_system_messages = MagicMock(return_value=[])
    context.get_context = AsyncMock(
        return_value=MagicMock(messages=stored or [], participants=[])
    )
    return context


@dataclass
class Delivery:
    message: PlatformMessage
    status: str | None = None
    in_rest: bool = True


@dataclass
class FakeRoom:
    """The platform side of one room: an ordered store behind the link's calls."""

    page_size: int = 100
    refuse_processing: set[str] = field(default_factory=set)
    unconfirmed_terminal: bool = False
    deliveries: dict[str, Delivery] = field(default_factory=dict)
    marks: list[tuple[str, str]] = field(default_factory=list)
    # Events the WebSocket delivers while the next page read is in flight.
    during_read: list[Any] = field(default_factory=list)
    deliver: Callable[[Any], Awaitable[None]] | None = None

    def post(
        self,
        message_id: str,
        *,
        sender_id: str = "alice",
        at: int | None = None,
        in_rest: bool = True,
        status: str | None = None,
    ) -> MessageEvent:
        """Store a message ``at`` seconds after the epoch and return its WS event."""
        offset = len(self.deliveries) if at is None else at
        moment = EPOCH + timedelta(seconds=offset)
        message = PlatformMessage(
            id=message_id,
            room_id=ROOM_ID,
            content=message_id,
            sender_id=sender_id,
            sender_type="User",
            sender_name=None,
            message_type="text",
            metadata=self.metadata(status),
            created_at=moment,
        )
        self.deliveries[message_id] = Delivery(message, status, in_rest)
        return make_message_event(
            room_id=ROOM_ID,
            msg_id=message_id,
            sender_id=sender_id,
            inserted_at=moment.isoformat(),
        )

    @staticmethod
    def metadata(status: str | None) -> dict[str, Any]:
        statuses = {AGENT_ID: {"status": status}} if status else {}
        return {"mentions": [], "delivery_status": statuses}

    def status_of(self, message_id: str) -> str | None:
        return self.deliveries[message_id].status

    def outstanding(self) -> list[PlatformMessage]:
        return [
            replace(d.message, metadata=self.metadata(d.status))
            for d in sorted(
                self.deliveries.values(),
                key=lambda d: (d.message.created_at, d.message.id),
            )
            if d.in_rest and d.status != DeliveryStatus.PROCESSED
        ]

    async def actionable_page(
        self, room_id: str, *, cursor: str | None = None
    ) -> ActionablePage:
        messages = self.outstanding()
        while self.during_read and self.deliver is not None:
            await self.deliver(self.during_read.pop(0))
        if cursor is not None:
            ids = [m.id for m in messages]
            messages = messages[ids.index(cursor) + 1 :] if cursor in ids else messages
        page, rest = messages[: self.page_size], messages[self.page_size :]
        return ActionablePage(page, page[-1].id if rest else None)

    async def mark_processing(self, room_id: str, message_id: str) -> bool:
        self.marks.append(("processing", message_id))
        if message_id in self.refuse_processing:
            return False
        self.deliveries[message_id].status = DeliveryStatus.PROCESSING
        return True

    async def mark_processed_result(
        self, room_id: str, message_id: str
    ) -> SettlementResult:
        return self.settle(DeliveryStatus.PROCESSED, message_id)

    async def mark_failed_result(
        self, room_id: str, message_id: str, error: str | None = None
    ) -> SettlementResult:
        return self.settle(DeliveryStatus.FAILED, message_id)

    def settle(self, status: DeliveryStatus, message_id: str) -> SettlementResult:
        if self.unconfirmed_terminal:
            return SettlementResult.UNCONFIRMED
        self.marks.append((status, message_id))
        self.deliveries[message_id].status = status
        return SettlementResult.ACCEPTED

    def link(self, rest: MagicMock) -> MagicMock:
        link = MagicMock()
        link.agent_id = AGENT_ID
        link.rest = rest
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(
                data=[make_participant_mock("alice", "Alice", "User")]
            )
        )
        link.get_actionable_page = AsyncMock(side_effect=self.actionable_page)
        link.get_next_message = AsyncMock(return_value=None)
        link.mark_processing = AsyncMock(side_effect=self.mark_processing)
        link.mark_processed = AsyncMock(return_value=True)
        link.mark_failed = AsyncMock(return_value=True)
        link.mark_processed_result = AsyncMock(side_effect=self.mark_processed_result)
        link.mark_failed_result = AsyncMock(side_effect=self.mark_failed_result)
        link.report_activity = AsyncMock(return_value=True)
        link.get_stale_processing_messages = AsyncMock(return_value=[])
        return link


@dataclass
class Turns:
    """What the host was asked to run, one entry per invocation.

    A non-message event is recorded by its type; ``fail_on`` makes any turn
    that includes one of those messages raise after it is recorded.
    """

    ids: list[tuple[str, ...]] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    gate: bool = False
    fail_on: set[str] = field(default_factory=set)
    # While False the host refuses ordinary input, as a busy adapter does.
    accepting: bool = True

    async def single(self, ctx: ExecutionContext, event: Any) -> None:
        await self.record(
            (event.payload.id if isinstance(event, MessageEvent) else event.type,)
        )

    async def batch(self, ctx: ExecutionContext, events: tuple[Any, ...]) -> None:
        await self.record(tuple(event.payload.id for event in events))

    async def record(self, ids: tuple[str, ...]) -> None:
        if not self.accepting:
            raise InputNotAccepted("busy")
        self.ids.append(ids)
        if self.gate:
            await self.release.wait()
        if self.fail_on.intersection(ids):
            raise RuntimeError("the model failed")


@dataclass
class ControlScript:
    """A host's control vocabulary: what it recognizes and what it consumes.

    ``recognized`` is read live, so a handler can change what the next
    candidate classifies as. ``declines`` are recognized messages the handler
    then refuses to consume (an expired request).
    """

    recognized: set[str] = field(default_factory=set)
    declines: set[str] = field(default_factory=set)
    handled: list[str] = field(default_factory=list)
    on_handled: Callable[[str], None] = lambda message_id: None

    def recognizes(self, ctx: ExecutionContext, event: MessageEvent) -> bool:
        return event.payload is not None and event.payload.id in self.recognized

    async def handle(self, ctx: ExecutionContext, event: MessageEvent) -> bool:
        message_id = _payload_id(event)
        if message_id in self.declines or message_id not in self.recognized:
            return False
        self.handled.append(message_id)
        self.on_handled(message_id)
        return True


def _payload_id(event: Any) -> str:
    return event.payload.id


@dataclass
class SharedCapacity:
    """An adapter's capacity line as the scheduler drives it: calls, in order."""

    calls: list[tuple[str, str, int]] = field(default_factory=list)

    def register_capacity_wait(self, *, room_id: str, generation: int) -> None:
        self.calls.append(("register", room_id, generation))

    def withdraw_capacity_wait(self, *, room_id: str, generation: int) -> None:
        self.calls.append(("withdraw", room_id, generation))

    def actions(self) -> list[str]:
        return [action for action, _, _ in self.calls]

    def generations(self) -> set[int]:
        return {generation for _, _, generation in self.calls}


@dataclass
class Room:
    platform: FakeRoom
    ctx: ExecutionContext
    turns: Turns

    async def deliver(self, *events: MessageEvent) -> None:
        for event in events:
            await self.ctx.on_event(event)

    async def until(self, condition: Callable[[], bool]) -> None:
        async with asyncio.timeout(WAIT_SECONDS):
            while not condition():
                await asyncio.sleep(0.005)

    async def settled(self, *message_ids: str) -> None:
        await self.until(
            lambda: all(
                self.platform.status_of(m) == DeliveryStatus.PROCESSED
                for m in message_ids
            )
        )


@contextlib.asynccontextmanager
async def started_room(
    platform: FakeRoom,
    rest: MagicMock,
    *,
    batching: bool = True,
    max_queued: int = 10,
    gate: bool = False,
    fail_on: frozenset[str] = frozenset(),
    arrived: tuple[MessageEvent, ...] = (),
    controls: ControlScript | None = None,
    capacity: SharedCapacity | None = None,
) -> AsyncIterator[Room]:
    turns = Turns(gate=gate, fail_on=set(fail_on))
    ctx = ExecutionContext(
        ROOM_ID,
        platform.link(rest),
        turns.single,
        config=SessionConfig(
            consume_all_queued=batching, max_queued_messages=max_queued
        ),
        agent_id=AGENT_ID,
        on_execute_batch=turns.batch if batching else None,
        on_message_control=controls.handle if controls else None,
        capacity=capacity,
        is_message_control=controls.recognizes if controls else None,
    )
    platform.deliver = ctx.on_event
    for event in arrived:
        await ctx.on_event(event)
    await ctx.start()
    try:
        yield Room(platform, ctx, turns)
    finally:
        turns.release.set()
        await ctx.stop(timeout=2)
