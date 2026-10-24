from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext, ExecutionHandler
from tests.conftest import make_message_event, mirror_settlement_results


@pytest.fixture
def mock_link() -> MagicMock:
    """Create a mock BandLink."""
    link = MagicMock()
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.get_next_message = AsyncMock(return_value=None)
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    return mirror_settlement_results(link)


@pytest.fixture
def history_link() -> MagicMock:
    """Create a mock BandLink with context data."""
    link = MagicMock()
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )

    # Mock context response
    mock_msg1 = MagicMock()
    mock_msg1.id = "msg-1"
    mock_msg1.content = "Hello"
    mock_msg1.sender_id = "user-1"
    mock_msg1.sender_type = "User"
    mock_msg1.sender_name = "Alice"
    mock_msg1.message_type = "text"
    mock_msg1.inserted_at = datetime.now(UTC).isoformat()

    mock_msg2 = MagicMock()
    mock_msg2.id = "msg-2"
    mock_msg2.content = "Hi there!"
    mock_msg2.sender_id = "agent-1"
    mock_msg2.sender_type = "Agent"
    mock_msg2.sender_name = "TestBot"
    mock_msg2.message_type = "text"
    mock_msg2.inserted_at = datetime.now(UTC).isoformat()

    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[mock_msg1, mock_msg2])
    )
    link.get_next_message = AsyncMock(return_value=None)
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    return mirror_settlement_results(link)


@pytest.fixture
def dummy_handler() -> ExecutionHandler:
    """Create a dummy execution handler."""

    async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        pass

    return handler


@pytest.fixture
def sample_event() -> MessageEvent:
    """Create a sample platform event."""
    return make_message_event(
        room_id="room-123",
        msg_id="msg-123",
        content="Hello",
        sender_id="user-456",
        sender_type="User",
    )
