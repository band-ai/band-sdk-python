"""Shared fixtures for ACP integration tests.

The ACP test doubles live in ``acp_toolkit`` (imported here and re-exported for
back-compat): ``FakeSpawn`` (low-level transport-seam spy) and ``FakeACPAgent`` +
``acp_adapter`` (a real-wire, in-process fake agent + driver). This module keeps the
pytest fixtures and the message/rest builders.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from band.core.types import MessageType, PlatformMessage
from band.integrations.acp.server_adapter import BandACPServerAdapter
from band.integrations.acp.types import ACPSessionState, PendingACPPrompt
from band.testing import FakeAgentTools

from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent as FakeACPAgent,  # re-exported for tests importing from conftest
)
from tests.integrations.acp.acp_toolkit import (
    FakeSpawn,
    Reply,
    acp_adapter as acp_adapter,  # re-exported
    make_acp_connection as make_acp_connection,  # re-exported
)


@pytest.fixture
def make_acp_transport() -> Callable[..., FakeSpawn]:
    """Factory for a :class:`FakeSpawn` wired with a scripted ACP connection.

    Defaults to advertising HTTP MCP; pass ``http=``/``sse=`` to script the
    capabilities the transport selection logic reads from ``initialize``.
    """

    def _make(*, http: bool = True, sse: bool = False) -> FakeSpawn:
        return FakeSpawn(conn=make_acp_connection(http=http, sse=sse))

    return _make


@pytest.fixture
def fake_agent() -> FakeACPAgent:
    """A fresh, unscripted in-process fake ACP agent.

    Script it in the test body (``fake_agent.will_say(...)`` / ``@fake_agent.on_prompt``)
    then drive it with ``acp_adapter(fake_agent)``.
    """
    return FakeACPAgent()


class ACPEditor:
    """Drive the ACP server boundary as an editor awaiting a room reply."""

    def __init__(self, client: AsyncMock) -> None:
        self._client = client
        self._bridge = BandACPServerAdapter()
        self._bridge.set_acp_client(client)
        self._tools = FakeAgentTools()
        self._room_id: str | None = None

    def awaiting_reply(self, room_id: str) -> ACPEditor:
        self._room_id = room_id
        self._bridge._pending_prompts[room_id] = PendingACPPrompt(session_id=room_id)
        return self

    async def forward_tool_activity(self, reply: Reply) -> None:
        if self._room_id is None:
            raise RuntimeError("Call awaiting_reply() before forwarding room activity")

        for activity in reply.transcript:
            if activity.message_type not in {
                MessageType.TOOL_CALL,
                MessageType.TOOL_RESULT,
            }:
                continue
            await self._bridge.on_message(
                make_platform_message(
                    activity.content,
                    room_id=self._room_id,
                    message_type=activity.message_type,
                ),
                self._tools,
                ACPSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id=self._room_id,
            )

    @property
    def updates(self) -> list[object]:
        return [
            call.kwargs["update"]
            for call in self._client.session_update.await_args_list
        ]


@pytest.fixture
def acp_editor(mock_acp_client: AsyncMock) -> ACPEditor:
    """An ACP editor that can receive a room reply through the server bridge."""
    return ACPEditor(mock_acp_client)


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


async def wait_for_pending_prompt(
    adapter: BandACPServerAdapter, room_id: str
) -> PendingACPPrompt:
    """Wait until a pending prompt is registered for a room.

    ``handle_prompt`` blocks until the peer replies, so tests dispatch it as
    a task and use this to wait for the prompt to reach the room.
    """
    while True:
        pending = adapter._pending_prompts.get(room_id)
        if pending is not None:
            return pending
        await asyncio.sleep(0)


def has_pending_prompt(adapter: BandACPServerAdapter, room_id: str) -> bool:
    """Whether a room currently has a prompt awaiting a reply."""
    return adapter._pending_prompts.get(room_id) is not None


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
