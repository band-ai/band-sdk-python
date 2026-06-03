"""Shared fixtures for ACP integration tests."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from band.core.types import PlatformMessage
from band.integrations.acp.types import ACPSessionState


def make_platform_message(
    content: str,
    room_id: str = "room-123",
    message_type: str = "text",
    sender_id: str = "peer-456",
    sender_name: str = "Test Peer",
) -> PlatformMessage:
    """Create a test PlatformMessage."""
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="Agent",
        sender_name=sender_name,
        message_type=message_type,
        metadata={},
        created_at=datetime.now(),
    )


def make_tool_call_message(
    name: str = "get_weather",
    args: dict | None = None,
    tool_call_id: str = "tc-123",
    room_id: str = "room-123",
) -> PlatformMessage:
    """Create a tool_call PlatformMessage with JSON content."""
    content = json.dumps(
        {
            "name": name,
            "args": args or {},
            "tool_call_id": tool_call_id,
        }
    )
    return make_platform_message(
        content=content,
        room_id=room_id,
        message_type="tool_call",
    )


def make_tool_result_message(
    name: str = "get_weather",
    output: str = "72F sunny",
    tool_call_id: str = "tc-123",
    is_error: bool = False,
    room_id: str = "room-123",
) -> PlatformMessage:
    """Create a tool_result PlatformMessage with JSON content."""
    content = json.dumps(
        {
            "name": name,
            "output": output,
            "tool_call_id": tool_call_id,
            "is_error": is_error,
        }
    )
    return make_platform_message(
        content=content,
        room_id=room_id,
        message_type="tool_result",
    )


@pytest.fixture
def mock_rest_client() -> MagicMock:
    """Create a mock AsyncRestClient with pre-configured responses."""
    client = MagicMock()

    # Mock chat creation
    mock_chat_response = MagicMock()
    mock_chat_response.data = MagicMock()
    mock_chat_response.data.id = "room-new-123"
    client.agent_api_chats.create_agent_chat = AsyncMock(
        return_value=mock_chat_response
    )

    # Mock participant listing
    mock_participants_response = MagicMock()
    mock_participants_response.data = []
    client.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=mock_participants_response
    )

    # Mock message creation
    client.agent_api_messages.create_agent_chat_message = AsyncMock()

    # Mock event creation
    client.agent_api_events.create_agent_chat_event = AsyncMock()

    return client


@pytest.fixture
def mock_acp_client() -> AsyncMock:
    """Create a mock ACP Client interface."""
    client = AsyncMock()
    client.session_update = AsyncMock()
    return client


@pytest.fixture
def sample_acp_session_state() -> ACPSessionState:
    """Create a pre-populated ACPSessionState."""
    return ACPSessionState(
        session_to_room={"session-1": "room-1", "session-2": "room-2"},
    )
