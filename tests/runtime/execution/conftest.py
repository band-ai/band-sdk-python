"""Execution context fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import (
    make_participant_mock,
    mirror_settlement_results,
)


@pytest.fixture
def mock_link():
    """Mock BandLink for testing ExecutionContext."""
    link = MagicMock()
    link.agent_id = "agent-123"

    # REST client mock
    link.rest = MagicMock()

    # Mock list_agent_chat_participants
    participant1 = make_participant_mock("user-1", "User One", "User")
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[participant1])
    )

    # Mock get_agent_chat_context
    msg1 = MagicMock()
    msg1.id = "msg-1"
    msg1.content = "Hello"
    msg1.sender_id = "user-1"
    msg1.sender_type = "User"
    msg1.sender_name = "User One"
    msg1.message_type = "text"
    msg1.inserted_at = "2024-01-01T00:00:00Z"
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[msg1])
    )

    # Mock message lifecycle methods (new in BandLink)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    mirror_settlement_results(link)
    link.get_next_message = AsyncMock(return_value=None)  # No backlog by default
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.report_activity = AsyncMock(return_value=True)

    return link


@pytest.fixture
def mock_handler():
    """Mock execution handler."""
    return AsyncMock()
