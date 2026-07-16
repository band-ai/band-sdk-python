"""Shared factories for the Letta adapter tests.

Mock builders for Letta API objects (messages, responses, agents,
conversations, MCP servers/tools, async streams) and platform messages,
used by both ``test_letta_adapter.py`` and ``test_letta_mcp.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from band.integrations.letta.prompts import render_tool_enforcement
from band.core.types import PlatformMessage


def make_platform_message(
    room_id: str = "room-1", content: str = "hello"
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(),
    )


def make_letta_message(msg_type: str, **kwargs: Any) -> MagicMock:
    """Create a fake Letta response message."""
    msg = MagicMock()
    msg.message_type = msg_type
    for key, value in kwargs.items():
        setattr(msg, key, value)
    return msg


def make_assistant_message(content: str = "Hello!") -> MagicMock:
    return make_letta_message("assistant_message", content=content)


def make_tool_call_message(
    tool_name: str = "band_send_message",
    arguments: str = '{"content": "Hi", "mentions": ["@alice"]}',
) -> MagicMock:
    tool_call = MagicMock()
    tool_call.name = tool_name
    tool_call.arguments = arguments
    return make_letta_message("tool_call_message", tool_call=tool_call)


def make_tool_return_message(
    tool_name: str = "band_send_message",
    tool_return: str = '{"status": "ok"}',
) -> MagicMock:
    return make_letta_message(
        "tool_return_message", tool_name=tool_name, tool_return=tool_return
    )


def make_letta_response(*messages: MagicMock) -> MagicMock:
    """Create a fake Letta API response."""
    resp = MagicMock()
    resp.messages = list(messages)
    return resp


def make_mock_mcp_server(server_id: str = "mcp-server-1") -> MagicMock:
    """Create a mock MCP server response."""
    server = MagicMock()
    server.id = server_id
    return server


def make_mock_mcp_tool(tool_id: str, tool_name: str) -> MagicMock:
    """Create a mock MCP tool response."""
    tool = MagicMock()
    tool.id = tool_id
    tool.name = tool_name
    return tool


def make_mock_agent(agent_id: str = "agent-123") -> MagicMock:
    """Create a mock agent response."""
    agent = MagicMock()
    agent.id = agent_id
    return agent


def make_mock_conversation(
    conversation_id: str = "conv-123", *, agent_id: str | None = None
) -> MagicMock:
    """Create a mock conversation response."""
    conv = MagicMock()
    conv.id = conversation_id
    if agent_id is not None:
        conv.agent_id = agent_id
    return conv


def make_mock_tool_page(*tools: MagicMock) -> MagicMock:
    """Create a mock paginated tool list response."""
    page = MagicMock()
    page.items = list(tools)
    return page


def make_fake_mcp_backend(port: int = 55321) -> MagicMock:
    """Create a fake self-hosted Band MCP backend (create_band_mcp_backend result)."""
    backend = MagicMock()
    backend.local_server = MagicMock()
    backend.local_server.port = port
    backend.allowed_tools = ["mcp__band__band_send_message"]
    backend.stop = AsyncMock()
    return backend


def default_enforcement(room_id: str | None = None) -> str:
    """The enforcement preamble with the default (band_*) tool names."""
    return render_tool_enforcement(
        "band_send_message", "band_send_event", room_id=room_id
    )


def make_mock_async_stream(*messages: MagicMock) -> Any:
    """Create a mock async stream yielding Letta messages."""

    class _AsyncStream:
        def __init__(self, stream_messages: list[MagicMock]) -> None:
            self._messages = stream_messages

        async def __aiter__(self) -> Any:
            for stream_message in self._messages:
                yield stream_message

    return _AsyncStream(list(messages))
