"""Pytest fixtures shared by the ClaudeSDKAdapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.types import PlatformMessage
from tests.adapters.claude_sdk.helpers import ClaudeApprovalRoom


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.send_failure = AsyncMock(return_value={"status": "sent"})
    tools.add_participant = AsyncMock(return_value={"id": "user-1"})
    tools.remove_participant = AsyncMock(return_value={"status": "removed"})
    tools.lookup_peers = AsyncMock(return_value={"peers": []})
    tools.get_participants = AsyncMock(return_value=[])
    return tools


@pytest.fixture
async def approval_room() -> AsyncIterator[Callable[..., ClaudeApprovalRoom]]:
    """Open a manual-approval room on a fresh adapter; tool calls still
    awaiting a decision when the test ends are cancelled."""
    rooms: list[ClaudeApprovalRoom] = []

    def open_room(**adapter_config: Any) -> ClaudeApprovalRoom:
        adapter = ClaudeSDKAdapter(approval_mode="manual", **adapter_config)
        rooms.append(room := ClaudeApprovalRoom(adapter))
        return room

    yield open_room
    for room in rooms:
        for request in room.requests:
            request.cancel()
