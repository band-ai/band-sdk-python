"""Shared helpers for tests/runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx

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
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.identifiers import UUID_ID


@asynccontextmanager
async def rest_client_over(
    handler: Callable[[httpx.Request], httpx.Response],
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
    preprocessor = DefaultPreprocessor()

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        inp = await preprocessor.process(ctx=ctx, event=event, agent_id=AGENT_ID)
        if inp is not None:
            await adapter.on_event(inp)

    return ExecutionContext(
        ROOM_ID,
        link,
        handler,
        config=config or SessionConfig(enable_context_hydration=False),
        agent_id=AGENT_ID,
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
