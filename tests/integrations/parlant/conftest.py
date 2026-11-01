"""Shared fixtures for the Parlant integration tests (parlant-free at import)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from band.integrations.parlant.ports import reserve_server_ports
from band.integrations.parlant.tools import create_parlant_tools
from band.testing import FakeAgentTools
from tests.integrations.parlant.custommodels import (
    AnswerInput,
    NoteInput,
    PaintInput,
    Receipt,
    TallyInput,
    answer,
    tally,
)
from tests.integrations.parlant.enginecontext import running_engine
from tests.integrations.parlant.guardcases import RecordInput, store_upstream_record
from tests.integrations.parlant.helpers import SESSION_ID, ToolServer
from tests.integrations.parlant.sessions import (
    clear_session_tools,  # noqa: F401 -- autouse fixture
)

LOOPBACK = "127.0.0.1"


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.send_message = AsyncMock()
    tools.send_event = AsyncMock()
    tools.no_reply = AsyncMock(return_value={"status": "no_reply"})
    tools.send_failure = AsyncMock()
    tools.add_participant = AsyncMock(return_value={"status": "added"})
    tools.remove_participant = AsyncMock()
    tools.lookup_peers = AsyncMock(
        return_value={
            "data": [{"name": "Agent1", "description": "Test agent", "type": "Agent"}],
            "metadata": {"page": 1, "total_pages": 1},
        }
    )
    tools.get_participants = AsyncMock(return_value=[{"name": "User1", "type": "User"}])
    tools.create_chatroom = AsyncMock(return_value="new-room-123")
    tools.list_room_files = AsyncMock(
        return_value={
            "data": [
                {
                    "id": "file-1",
                    "name": "report.txt",
                    "content_type": "text/plain",
                    "bytes": 42,
                }
            ],
            "next_cursor": None,
        }
    )
    tools.read_room_file = AsyncMock(
        return_value={
            "name": "report.txt",
            "content_type": "text/plain",
            "bytes": 42,
            "text": "hello world",
        }
    )
    tools.send_room_file = AsyncMock(
        return_value={
            "attachment": {"id": "file-2", "name": "notes.txt"},
            "message_id": "msg-1",
        }
    )
    return tools


@pytest.fixture
def mock_context():
    """Create mock ToolContext.

    Uses ``SimpleNamespace`` so that accessing any attribute not
    explicitly set raises ``AttributeError`` — this catches tests
    that accidentally depend on attributes beyond ``session_id``.
    ``MagicMock(spec=ToolContext)`` is not used because ``ToolContext``
    lives in ``parlant.core.tools`` which may not be installed.
    """
    return SimpleNamespace(session_id=SESSION_ID)


@pytest.fixture
def parlant_tools():
    """Create Parlant tools from the real create_parlant_tools."""
    tools = create_parlant_tools()
    # Build a dict mapping tool name to the tool's function
    return {entry.tool.name: entry.function for entry in tools}


@pytest_asyncio.fixture(loop_scope="function")
async def plugin_server() -> AsyncIterator[ToolServer]:
    """A real Parlant ``PluginServer``, the boundary the engine calls tools through."""
    # Deferred so this conftest imports without parlant; a plain import (not
    # importorskip) fails loudly in a venv the parlant CI job doesn't cover.
    from parlant.core.services.tools.plugins import (  # noqa: PLC0415
        PluginServer,
    )

    port = reserve_server_ports(LOOPBACK).tool_service_port
    server = PluginServer(tools=[], port=port, host=LOOPBACK, hosted=True)
    async with (
        server,
        httpx.AsyncClient(base_url=server.url) as client,
    ):
        try:
            yield ToolServer(server=server, client=client)
        finally:
            await server.shutdown()


@pytest_asyncio.fixture(loop_scope="function")
async def bound_room():
    """Bind ``SESSION_ID`` to a room, so calls on it resolve their tools."""
    async with running_engine(SESSION_ID, FakeAgentTools()):
        yield


@pytest.fixture
def received() -> list[PaintInput]:
    """Every validated input the paint handler ran with."""
    return []


@pytest_asyncio.fixture(loop_scope="function")
async def custom_server(plugin_server, received):
    async def paint(args: PaintInput) -> dict[str, object]:
        received.append(args)
        return {"painted": args.wall}

    async def note(args: NoteInput) -> Receipt:
        return Receipt(note=args.text)

    await plugin_server.enable(
        create_parlant_tools(
            custom_tools=[
                (PaintInput, paint),
                (AnswerInput, answer),
                (NoteInput, note),
                (TallyInput, tally),
            ]
        )
    )
    return plugin_server


@pytest.fixture
def guarded_tools() -> dict[str, Any]:
    """The Band tools plus one custom tool, each by name as its guarded function."""
    entries = create_parlant_tools(custom_tools=[(RecordInput, store_upstream_record)])
    return {entry.tool.name: entry.function for entry in entries}
