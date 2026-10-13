"""Shared helpers for tests/runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx

from band.agent import Agent
from band.client.rest import AsyncRestClient
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.protocols import TURN_FAILURE_PROVIDER
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.identifiers import UUID_ID


@asynccontextmanager
async def rest_client_over(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
) -> AsyncIterator[AsyncRestClient]:
    """A real ``AsyncRestClient`` whose HTTP boundary is ``handler`` instead
    of the network, so request building and response parsing run through the
    real dependency rather than being simulated."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as httpx_client:
        yield AsyncRestClient(api_key="test-key", httpx_client=httpx_client)


@asynccontextmanager
async def memory_client() -> AsyncIterator[tuple[AsyncRestClient, list[httpx.Request]]]:
    """Record real memory requests and return an item both surfaces can parse."""
    requests: list[httpx.Request] = []
    item = {
        "id": UUID_ID,
        "content": "remember this",
        "system": MemorySystem.WORKING,
        "type": MemoryType.SEMANTIC,
        "segment": MemorySegment.USER,
        "scope": MemoryStoreScope.AGENT,
        "inserted_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": item})

    async with rest_client_over(answer) as rest:
        yield rest, requests


AGENT_ID = "agent-123"
ROOM_ID = "room-123"


def run_through(
    adapter: SimpleAdapter[Any],
    link: MagicMock,
    *,
    config: SessionConfig | None = None,
) -> ExecutionContext:
    agent = Agent(runtime=MagicMock(agent_id=AGENT_ID), adapter=adapter)

    return ExecutionContext(
        ROOM_ID,
        link,
        agent._on_execute,
        config=config or SessionConfig(enable_context_hydration=False),
        agent_id=AGENT_ID,
        on_message_control=agent._on_control,
    )


async def deliver(ctx: ExecutionContext, path: str) -> None:
    """Deliver one user message live over the socket, or from the backlog."""
    match path:
        case "live":
            await ctx._process_event(
                make_message_event(room_id=ROOM_ID, sender_id="user-1")
            )
        case "backlog":
            await ctx._process_backlog_message(
                PlatformMessage(
                    id="msg-backlog",
                    room_id=ROOM_ID,
                    content="@agent hi",
                    sender_id="user-1",
                    sender_type="User",
                    sender_name="User One",
                    message_type="text",
                    metadata={},
                    created_at=datetime.now(UTC),
                )
            )


def failure_posts(link: MagicMock) -> list[tuple[str, str]]:
    """Every attempted failure post, as (provider, text), in order."""
    return [
        (
            call.kwargs["event"].metadata["failure"]["provider"],
            call.kwargs["event"].content,
        )
        for call in link.rest.agent_api_events.create_agent_chat_event.call_args_list
        if call.kwargs["event"].metadata and "failure" in call.kwargs["event"].metadata
    ]


def runtime_failures(link: MagicMock) -> list[tuple[str, str]]:
    return [post for post in failure_posts(link) if post[0] == TURN_FAILURE_PROVIDER]


async def resist_cancellation_until(release: asyncio.Event) -> None:
    """Ignore every cancel until ``release`` is set, then finish cancelled."""
    while not release.is_set():
        with suppress(asyncio.CancelledError):
            await release.wait()
    raise asyncio.CancelledError


# An activity report as (requested working, platform recorded working).
ActivityReport = tuple[bool, bool]
KEEP_ALIVE: ActivityReport = (True, True)
STOP_ECHO: ActivityReport = (True, False)
IDLE: ActivityReport = (False, False)


class LifecyclePlatform:
    """A controlled HTTP peer for the real Fern lifecycle and posting client."""

    def __init__(self, *, stopped: bool = False) -> None:
        self.stopped = stopped
        self.messages: list[dict[str, Any]] = []
        self.accepted_marks: list[tuple[str, str]] = []
        self.requested_marks: list[tuple[str, str]] = []
        self.posts: list[str] = []
        self.processing_list_reads = 0
        self.next_reads: list[float] = []
        self.actionable_cursors: list[str | None] = []
        self.activity_reports: list[ActivityReport] = []

    def add_message(self, message_id: str, *, content: str = "hello") -> None:
        self.messages.append(
            {
                "id": message_id,
                "chat_room_id": ROOM_ID,
                "content": content,
                "sender_id": "user-1",
                "sender_type": "User",
                "sender_name": "User",
                "message_type": "text",
                "metadata": {},
                "inserted_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        tail = path.rsplit("/", 1)[-1]
        if tail == "activity":
            working = json.loads(request.content)["working"]
            recorded = working and not self.stopped
            self.activity_reports.append((working, recorded))
            return httpx.Response(200, json={"data": {"working": recorded}})
        if tail == "me":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": AGENT_ID,
                        "name": "Agent",
                        "handle": "agent",
                        "owner_uuid": "user-1",
                        "feature_flags": {},
                        "inserted_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                },
            )
        if tail == "messages" and request.method == "GET":
            if request.url.params.get("status") == "processing":
                self.processing_list_reads += 1
                return httpx.Response(
                    200, json={"data": self.messages, "metadata": {"total_pages": 1}}
                )
            cursor = request.url.params.get("cursor")
            self.actionable_cursors.append(cursor)
            start = next(
                (i + 1 for i, m in enumerate(self.messages) if m["id"] == cursor), 0
            )
            limit = int(request.url.params.get("limit", "100"))
            page = self.messages[start : start + limit]
            has_more = start + limit < len(self.messages)
            return httpx.Response(
                200,
                json={
                    "data": page,
                    "metadata": {
                        "limit": limit,
                        "has_more": has_more,
                        "next_cursor": page[-1]["id"] if has_more else None,
                    },
                },
            )
        if tail == "next":
            self.next_reads.append(asyncio.get_running_loop().time())
            if self.stopped or not self.messages:
                return httpx.Response(204)
            return httpx.Response(200, json={"data": self.messages[0]})
        if tail in {"processing", "processed", "failed"}:
            message_id = path.split("/")[-2]
            self.requested_marks.append((message_id, tail))
            if self.stopped:
                return httpx.Response(204)
            self.accepted_marks.append((message_id, tail))
            if tail == "processed":
                self.messages = [m for m in self.messages if m["id"] != message_id]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": message_id,
                        "attempt_number": 1,
                        "status": tail,
                        "success": True,
                    }
                },
            )
        if request.method == "POST" and tail in {"messages", "events"}:
            self.posts.append(tail)
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": "forbidden",
                        "message": "Agent execution is stopped; cannot post " + tail,
                        "request_id": "request-1",
                    }
                },
            )
        return httpx.Response(
            200, json={"data": [], "metadata": {"has_more": False, "limit": 50}}
        )

    def marked(self, status: str) -> list[str]:
        return [mid for mid, mark in self.accepted_marks if mark == status]

    def requested(self, status: str) -> list[str]:
        return [mid for mid, mark in self.requested_marks if mark == status]


#: A room participant the platform lists.
ALICE = {"id": "user-456", "name": "Alice", "handle": "alice", "type": "User"}


class PostingPlatform(LifecyclePlatform):
    """A running room whose participants are listed and whose posts land."""

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return httpx.Response(
                200,
                json={"data": [ALICE], "metadata": {"has_more": False, "limit": 50}},
            )
        if request.method == "POST" and tail == "events":
            return httpx.Response(
                201, json={"data": {"id": "e-1", "message_type": "event"}}
            )
        if request.method == "POST" and tail == "messages":
            return httpx.Response(201, json={"data": {"id": "m-1", "recipients": []}})
        return super().answer(request)


class ClaimGate:
    """Hold one real HTTP processing response across a control or shutdown."""

    def __init__(
        self,
        peer: LifecyclePlatform,
        message_id: str,
        *,
        response: httpx.Response | None = None,
        suppress_cancel: bool = False,
    ) -> None:
        self.peer = peer
        self.message_id = message_id
        self.response = response
        self.suppress_cancel = suppress_cancel
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def answer(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/{self.message_id}/processing"):
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    if not self.suppress_cancel:
                        raise
            if self.response is not None:
                self.peer.requested_marks.append((self.message_id, "processing"))
                return self.response
        return self.peer.answer(request)


class ResponseGate:
    """Hold a real HTTP response across an execution control boundary."""

    def __init__(self, peer: LifecyclePlatform, suffix: str) -> None:
        self.peer = peer
        self.suffix = suffix
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def answer(self, request: httpx.Request) -> httpx.Response:
        response = self.peer.answer(request)
        if request.url.path.endswith(self.suffix):
            self.entered.set()
            await self.release.wait()
        return response


class RoomCleanup:
    """The adapter's room cleanup, held or failed on demand."""

    def __init__(self) -> None:
        self.rooms: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failing = False
        self.failing_rooms: set[str] = set()

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    async def __call__(self, room_id: str) -> None:
        self.entered.set()
        await self.release.wait()
        if self.failing or room_id in self.failing_rooms:
            raise RuntimeError("room cleanup failed")
        self.rooms.append(room_id)


class HeldExecution:
    """A custom execution whose start and event delivery can be held, and
    whose start can fail."""

    def __init__(self, room_id: str, gate: StartGate, number: int) -> None:
        self.room_id = room_id
        self.gate = gate
        self.number = number
        self.started = False
        self.stopped = False
        self.stop_timeout: float | None = None
        self.resyncs = 0
        self.events: list[object] = []

    async def start(self) -> None:
        self.gate.entered.set()
        await self.gate.release.wait()
        if self.gate.failures:
            self.gate.failures -= 1
            raise RuntimeError("execution start failed")
        self.started = True
        self.gate.journal.append(f"start {self.number}")

    async def stop(self, timeout: float | None = None) -> bool:
        self.stopped = True
        self.stop_timeout = timeout
        self.gate.journal.append(f"stop {self.number}")
        return self.gate.graceful

    async def on_event(self, event: object) -> None:
        self.gate.delivering.set()
        await self.gate.deliver.wait()
        self.events.append(event)

    async def request_resync(self) -> None:
        self.resyncs += 1


class StartGate:
    """Builds ``HeldExecution``s; each start waits for ``release`` and each
    event delivery for ``deliver``."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.delivering = asyncio.Event()
        self.deliver = asyncio.Event()
        self.deliver.set()
        self.failures = 0
        self.graceful = True
        self.built: list[HeldExecution] = []
        # Lifecycle of every execution built, numbered in build order.
        self.journal: list[str] = []

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    def hold_delivery(self) -> None:
        self.delivering.clear()
        self.deliver.clear()

    def __call__(self, room_id: str, link, *, hub_room_id=None) -> HeldExecution:
        execution = HeldExecution(room_id, self, number=len(self.built) + 1)
        self.built.append(execution)
        return execution


@asynccontextmanager
async def running_execution(ctx: ExecutionContext) -> AsyncIterator[ExecutionContext]:
    """Exercise admission, handler supervision and shutdown through the room loop."""
    await ctx.start()
    try:
        yield ctx
    finally:
        await ctx.stop()
