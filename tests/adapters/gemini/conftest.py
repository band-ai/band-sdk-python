"""Provide Gemini adapter fixtures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import types

from band.adapters.gemini import GeminiAdapter
from band.core.types import PlatformMessage
from band.runtime.tools import ToolCallOutcome


@pytest.fixture
def sample_message() -> PlatformMessage:
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
def mock_tools() -> MagicMock:
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.send_failure = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call_structured = AsyncMock(
        return_value=ToolCallOutcome(value={"status": "success"}, ok=True)
    )
    return tools


@pytest.fixture
def scripted_adapter() -> Callable[..., Awaitable[GeminiAdapter]]:
    """Build a started adapter whose client returns ``responses`` in order."""

    async def build(
        *responses: types.GenerateContentResponse, **features: Any
    ) -> GeminiAdapter:
        adapter = GeminiAdapter(**features)
        await adapter.on_started("TestBot", "Test bot")
        adapter.client = MagicMock()
        adapter.client.aio.models.generate_content = AsyncMock(
            side_effect=list(responses)
        )
        return adapter

    return build
