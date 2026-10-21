"""Fixtures for AgentRuntime tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.runtime import AgentRuntime
from tests.runtime.helpers import RoomCleanup, StartGate


@pytest.fixture
def mock_link():
    """Mock BandLink for testing AgentRuntime."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    # Async methods
    link.connect = AsyncMock()
    link.disconnect = AsyncMock()
    link.run_forever = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    # REST client mock
    link.rest = MagicMock()
    link.rest.agent_api_chats = MagicMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=[])
    )

    # Message lifecycle methods
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.get_actionable_messages = AsyncMock(return_value=[])
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.report_activity = AsyncMock(return_value=True)

    # Make link iterable for async for
    async def empty_aiter():
        return
        yield

    link.__aiter__ = lambda self: empty_aiter()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )

    return link


@pytest.fixture
def mock_handler():
    """Mock execution handler."""
    return AsyncMock()


@pytest.fixture
def room_cleanup() -> RoomCleanup:
    return RoomCleanup()


@pytest.fixture
async def runtime(mock_link, mock_handler, room_cleanup):
    runtime = AgentRuntime(
        mock_link, "agent-123", mock_handler, on_session_cleanup=room_cleanup
    )
    yield runtime
    room_cleanup.failing = False
    room_cleanup.release.set()
    await runtime.stop()


@pytest.fixture
def start_gate() -> StartGate:
    return StartGate()


@pytest.fixture
async def held_runtime(mock_link, mock_handler, room_cleanup, start_gate):
    runtime = AgentRuntime(
        mock_link,
        "agent-123",
        mock_handler,
        execution_factory=start_gate,
        on_session_cleanup=room_cleanup,
    )
    yield runtime
    start_gate.release.set()
    room_cleanup.failing = False
    room_cleanup.release.set()
    await runtime.stop()
