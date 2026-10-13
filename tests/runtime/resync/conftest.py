"""Fixtures for reconciliation and resync tests."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
)
from tests.runtime.resync.kit import ReconciledRoom


@pytest.fixture
def mock_link():
    """BandLink mock configured for ExecutionContext tests."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    link.connect = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_chats = MagicMock()
    api_response = MagicMock()
    api_response.data = []
    api_response.metadata = MagicMock()
    api_response.metadata.total_pages = None
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(return_value=api_response)

    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])

    async def empty_aiter():
        return
        yield

    link.__aiter__ = lambda self: empty_aiter()

    return link


@pytest.fixture
def mock_handler():
    return AsyncMock()


@pytest_asyncio.fixture(loop_scope="function")
async def reconciled_room(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[ReconciledRoom]:
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)
    peer = LifecyclePlatform()
    invoked: list[str] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        if isinstance(event, MessageEvent):
            invoked.append(event.payload.id)

    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = ExecutionContext(
            ROOM_ID,
            link,
            handler,
            agent_id=AGENT_ID,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        try:
            yield ctx, peer, invoked
        finally:
            await ctx.stop()
