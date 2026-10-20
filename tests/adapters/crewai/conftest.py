from __future__ import annotations

import contextlib
import importlib
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.types import PlatformMessage

if TYPE_CHECKING:
    from band.adapters.crewai import CrewAIAdapter as CrewAIAdapterType

from tests.adapters.crewai.kit import MockBaseTool


@pytest.fixture
def crewai_mocks(monkeypatch):

    mock_crewai_module = MagicMock()
    mock_crewai_tools_module = MagicMock()
    mock_nest_asyncio = MagicMock()

    mock_crewai_module.Agent = MagicMock()
    mock_crewai_module.LLM = MagicMock()
    mock_crewai_tools_module.BaseTool = MockBaseTool

    # `_nest_asyncio_applied` is process-global. Any test running with a mocked
    # nest_asyncio can flip it True without anything actually being patched, which
    # then silently disables the real patch for every later test — so isolate it.
    runtime = importlib.import_module("band.integrations.crewai.runtime")
    monkeypatch.setattr(runtime, "_nest_asyncio_applied", False)

    # No sys.modules surgery: every crewai import in the band modules is
    # TYPE_CHECKING-only or function-local, so they pick the mocks up at call time.
    monkeypatch.setitem(sys.modules, "crewai", mock_crewai_module)
    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    yield mock_crewai_module


@pytest.fixture
def CrewAIAdapter(crewai_mocks) -> type[CrewAIAdapterType]:

    module = importlib.import_module("band.adapters.crewai")
    return module.CrewAIAdapter


@pytest.fixture
def sample_message():
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
    tools.get_tool_schemas = MagicMock(return_value=[])
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.send_failure = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    tools.add_participant = AsyncMock(
        return_value={"id": "123", "name": "Test", "status": "added"}
    )
    tools.remove_participant = AsyncMock(
        return_value={"id": "123", "name": "Test", "status": "removed"}
    )
    tools.get_participants = AsyncMock(
        return_value=[{"id": "123", "name": "Alice", "type": "User"}]
    )
    tools.lookup_peers = AsyncMock(
        return_value={
            "data": [],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "total_count": 0,
                "total_pages": 0,
            },
        }
    )
    tools.create_chatroom = AsyncMock(return_value="new-room-123")
    tools.list_contacts = AsyncMock(
        return_value={
            "data": [{"id": "contact-1", "handle": "@alice", "name": "Alice"}],
            "metadata": {"page": 1, "page_size": 50, "total_count": 1},
        }
    )
    tools.add_contact = AsyncMock(
        return_value={"id": "request-1", "handle": "@alice", "status": "pending"}
    )
    tools.remove_contact = AsyncMock(
        return_value={"id": "contact-1", "handle": "@alice", "status": "removed"}
    )
    tools.list_contact_requests = AsyncMock(
        return_value={
            "data": {
                "received": [{"id": "request-1", "from_handle": "@alice"}],
                "sent": [],
            },
            "metadata": {"page": 1, "page_size": 50, "total_count": 1},
        }
    )
    tools.respond_contact_request = AsyncMock(
        return_value={"id": "request-1", "status": "approved"}
    )
    tools.list_memories = AsyncMock(
        return_value={
            "data": [{"id": "memory-1", "content": "remember this"}],
            "meta": {"page_size": 1, "total_count": 1},
        }
    )
    tools.store_memory = AsyncMock(return_value={"id": "memory-1", "status": "stored"})
    tools.get_memory = AsyncMock(
        return_value={"id": "memory-1", "content": "remember this"}
    )
    tools.supersede_memory = AsyncMock(
        return_value={"id": "memory-1", "status": "superseded"}
    )
    tools.archive_memory = AsyncMock(
        return_value={"id": "memory-1", "status": "archived"}
    )
    return tools


@pytest.fixture
def mock_crewai_agent():
    mock_result = MagicMock()
    mock_result.raw = "Hello! I'm here to help."

    mock_agent = MagicMock()
    mock_agent.kickoff_async = AsyncMock(return_value=mock_result)
    return mock_agent


@pytest.fixture
def room_context(crewai_mocks, mock_tools):
    """Context manager fixture for setting up room context in tests.

    Usage:
        with room_context("room-123"):
            # tool execution code here
    """

    module = importlib.import_module("band.adapters.crewai")

    @contextlib.contextmanager
    def _room_context(room_id: str = "room-123"):
        module._current_room_context.set((room_id, mock_tools))
        try:
            yield
        finally:
            module._current_room_context.set(None)

    return _room_context
