"""Tests for LettaAdapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    _LETTA_RELAY_NOTE,
    _LETTA_TOOL_ENFORCEMENT,
    _RoomContext,
)
from band.converters.letta import LettaSessionState
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools


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


def _make_letta_message(msg_type: str, **kwargs: Any) -> MagicMock:
    """Create a fake Letta response message."""
    msg = MagicMock()
    msg.message_type = msg_type
    for key, value in kwargs.items():
        setattr(msg, key, value)
    return msg


def _make_assistant_message(content: str = "Hello!") -> MagicMock:
    return _make_letta_message("assistant_message", content=content)


def _make_tool_call_message(
    tool_name: str = "band_send_message",
    arguments: str = '{"content": "Hi", "mentions": ["@alice"]}',
) -> MagicMock:
    tool_call = MagicMock()
    tool_call.name = tool_name
    tool_call.arguments = arguments
    return _make_letta_message("tool_call_message", tool_call=tool_call)


def _make_tool_return_message(
    tool_name: str = "band_send_message",
    tool_return: str = '{"status": "ok"}',
) -> MagicMock:
    return _make_letta_message(
        "tool_return_message", tool_name=tool_name, tool_return=tool_return
    )


def _make_letta_response(*messages: MagicMock) -> MagicMock:
    """Create a fake Letta API response."""
    resp = MagicMock()
    resp.messages = list(messages)
    return resp


def _make_mock_mcp_server(server_id: str = "mcp-server-1") -> MagicMock:
    """Create a mock MCP server response."""
    server = MagicMock()
    server.id = server_id
    return server


def _make_mock_mcp_tool(tool_id: str, tool_name: str) -> MagicMock:
    """Create a mock MCP tool response."""
    tool = MagicMock()
    tool.id = tool_id
    tool.name = tool_name
    return tool


def _make_mock_agent(agent_id: str = "agent-123") -> MagicMock:
    """Create a mock agent response."""
    agent = MagicMock()
    agent.id = agent_id
    return agent


def _make_mock_conversation(conversation_id: str = "conv-123") -> MagicMock:
    """Create a mock conversation response."""
    conv = MagicMock()
    conv.id = conversation_id
    return conv


def _make_mock_tool_page(*tools: MagicMock) -> MagicMock:
    """Create a mock paginated tool list response."""
    page = MagicMock()
    page.items = list(tools)
    return page


def _make_mock_async_stream(*messages: MagicMock) -> Any:
    """Create a mock async stream yielding Letta messages."""

    class _AsyncStream:
        def __init__(self, stream_messages: list[MagicMock]) -> None:
            self._messages = stream_messages

        async def __aiter__(self) -> Any:
            for stream_message in self._messages:
                yield stream_message

    return _AsyncStream(list(messages))


# ──────────────────────────────────────────────────────────────────────
# Initialization
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterInit:
    def test_default_config(self) -> None:
        adapter = LettaAdapter()
        assert adapter.config == LettaAdapterConfig()
        assert adapter.config.base_url == "https://api.letta.com"
        assert adapter.config.provider_key is None
        assert adapter.config.project is None
        assert adapter.config.mode == "per_room"
        assert adapter.config.mcp_server_url == "http://localhost:8002/sse"
        assert adapter.config.mcp_server_name == "band"

    def test_custom_config(self) -> None:
        config = LettaAdapterConfig(
            base_url="http://custom:8283",
            provider_key="sk-test",
            mode="shared",
            mcp_server_url="http://mcp:9000/sse",
            enable_execution_reporting=True,
        )
        adapter = LettaAdapter(config=config)
        assert adapter.config.base_url == "http://custom:8283"
        assert adapter.config.provider_key == "sk-test"
        assert adapter.config.mode == "shared"
        assert adapter.config.enable_execution_reporting is True

    def test_cloud_defaults(self) -> None:
        """Default config targets Letta Cloud."""
        config = LettaAdapterConfig()
        assert config.base_url == "https://api.letta.com"
        assert config.provider_key is None
        assert config.project is None

    def test_no_client_tools_attribute(self) -> None:
        """MCP adapter has no _client_tools attribute."""
        adapter = LettaAdapter()
        assert not hasattr(adapter, "_client_tools")


# ──────────────────────────────────────────────────────────────────────
# on_started
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterOnStarted:
    @pytest.mark.asyncio
    async def test_on_started_creates_client_and_registers_mcp(self) -> None:
        adapter = LettaAdapter()

        mock_client = AsyncMock()
        mock_server = _make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_tools = [
            _make_mock_mcp_tool("t1", "band_send_message"),
            _make_mock_mcp_tool("t2", "band_send_event"),
        ]
        mock_client.mcp_servers.tools.list.return_value = mock_tools

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="https://api.letta.com",
        )
        mock_client.mcp_servers.create.assert_called_once_with(
            server_name="band",
            config={
                "mcp_server_type": "sse",
                "server_url": "http://localhost:8002/sse",
            },
        )
        assert adapter._mcp_server_id == mock_server.id
        assert adapter._mcp_tool_ids == ["t1", "t2"]
        assert adapter._system_prompt  # non-empty

    @pytest.mark.asyncio
    async def test_on_started_forwards_cloud_params(self) -> None:
        """provider_key and project are forwarded to AsyncLetta when configured."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url="https://api.letta.com",
                provider_key="letta-key-123",
                project="my-project",
            )
        )

        mock_client = AsyncMock()
        mock_server = _make_mock_mcp_server()
        mock_client.mcp_servers.create.return_value = mock_server
        mock_client.mcp_servers.tools.list.return_value = []

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_letta_module.AsyncLetta.assert_called_once_with(
            base_url="https://api.letta.com",
            api_key="letta-key-123",
            project="my-project",
        )

    @pytest.mark.asyncio
    async def test_on_started_mcp_registration_failure_raises(self) -> None:
        adapter = LettaAdapter()

        mock_client = AsyncMock()
        mock_client.mcp_servers.create.side_effect = ConnectionError("refused")

        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            with pytest.raises(RuntimeError, match="MCP server registration failed"):
                await adapter.on_started("TestBot", "A test bot")

    @pytest.mark.asyncio
    async def test_on_started_import_error(self) -> None:
        adapter = LettaAdapter()

        with patch.dict("sys.modules", {"letta_client": None}):
            with pytest.raises(ImportError, match="letta-client is required"):
                await adapter.on_started("TestBot", "A test bot")


# ──────────────────────────────────────────────────────────────────────
# on_message (per_room mode)
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterOnMessagePerRoom:
    """Tests for per_room mode message handling."""

    @pytest.fixture
    def adapter_with_client(self) -> tuple[LettaAdapter, AsyncMock]:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"
        adapter._mcp_tool_ids = ["t1", "t2"]
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_basic_message_creates_agent_and_sends(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        mock_agent = _make_mock_agent("agent-1")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("Hello!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Agent was created
        mock_client.agents.create.assert_called_once()
        # MCP tools were attached
        assert mock_client.agents.tools.attach.call_count == 2
        # Message was sent via direct agent API (not conversations)
        mock_client.agents.messages.create.assert_called_once()
        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "agent-1"
        assert "conversation_id" not in call_kwargs

    @pytest.mark.asyncio
    async def test_auto_relay_when_no_send_message(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        # Setup room with agent
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("I'll help you!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Auto-relay should have sent the message
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "I'll help you!"

    @pytest.mark.asyncio
    async def test_skip_auto_relay_when_send_message_used(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_tool_call_message("band_send_message"),
            _make_tool_return_message("band_send_message"),
            _make_assistant_message("Done!"),
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # No auto-relay — agent used send_message via MCP
        assert len(tools.messages_sent) == 0

    @pytest.mark.asyncio
    async def test_timeout_reports_error(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        adapter.config.turn_timeout_s = 0.01

        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        async def slow_response(**kwargs: Any) -> MagicMock:
            await asyncio.sleep(1)
            return _make_letta_response()

        mock_client.agents.messages.create.side_effect = slow_response

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert "timed out" in error_events[0]["content"]

    @pytest.mark.asyncio
    async def test_participants_and_contacts_injected(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("Got it.")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            "Participants: Alice, Bob",
            "Contacts: Charlie",
            is_session_bootstrap=False,
            room_id="room-1",
        )

        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        content = call_kwargs["messages"][0]["content"]
        assert "[System]: Participants: Alice, Bob" in content
        assert "[System]: Contacts: Charlie" in content

    @pytest.mark.asyncio
    async def test_agent_resume_from_history(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        mock_client.agents.retrieve.return_value = _make_mock_agent("history-agent")
        mock_client.agents.tools.list.return_value = _make_mock_tool_page()
        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("Resumed!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState(agent_id="history-agent")

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Agent was retrieved, not created
        mock_client.agents.retrieve.assert_called_once_with("history-agent")
        mock_client.agents.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_uninitialized_client_reports_error(self) -> None:
        adapter = LettaAdapter()
        # _client is None

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert "not initialized" in error_events[0]["content"]


# ──────────────────────────────────────────────────────────────────────
# on_message (shared mode)
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterSharedMode:
    """Tests for shared mode with Conversations API."""

    @pytest.fixture
    def shared_adapter(self) -> tuple[LettaAdapter, AsyncMock]:
        config = LettaAdapterConfig(mode="shared")
        adapter = LettaAdapter(config=config)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"
        adapter._mcp_tool_ids = ["t1", "t2"]
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_shared_mode_creates_agent_and_conversation(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = shared_adapter

        mock_agent = _make_mock_agent("shared-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_conv = _make_mock_conversation("conv-room1")
        mock_client.conversations.create.return_value = mock_conv
        mock_client.conversations.messages.create.return_value = (
            _make_mock_async_stream(_make_assistant_message("Hi from shared!"))
        )

        tools = FakeAgentTools()
        msg = make_platform_message(room_id="room-1")
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # One agent created
        mock_client.agents.create.assert_called_once()
        # Conversation created for this room
        mock_client.conversations.create.assert_called_once_with(
            agent_id="shared-agent",
        )
        # Message sent through room-scoped conversation endpoint
        call_kwargs = mock_client.conversations.messages.create.call_args.kwargs
        assert call_kwargs["conversation_id"] == "conv-room1"
        assert adapter._shared_agent_id == "shared-agent"

    @pytest.mark.asyncio
    async def test_shared_mode_reuses_agent_for_second_room(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = shared_adapter

        # Setup: first room already connected
        adapter._shared_agent_id = "shared-agent"
        adapter._rooms["room-1"] = _RoomContext(
            agent_id="shared-agent", conversation_id="conv-1"
        )

        mock_conv2 = _make_mock_conversation("conv-room2")
        mock_client.conversations.create.return_value = mock_conv2
        mock_client.conversations.messages.create.return_value = (
            _make_mock_async_stream(_make_assistant_message("Hi room 2!"))
        )

        tools = FakeAgentTools()
        msg = make_platform_message(room_id="room-2")
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-2",
        )

        # Agent was NOT created again
        mock_client.agents.create.assert_not_called()
        # But a new conversation was created
        mock_client.conversations.create.assert_called_once_with(
            agent_id="shared-agent",
        )
        assert adapter._rooms["room-2"].conversation_id == "conv-room2"

    @pytest.mark.asyncio
    async def test_shared_mode_resumes_existing_agent(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = shared_adapter
        adapter.config.agent_id = "pre-existing-agent"

        mock_client.agents.retrieve.return_value = _make_mock_agent(
            "pre-existing-agent"
        )
        mock_client.agents.tools.list.return_value = _make_mock_tool_page()
        mock_conv = _make_mock_conversation("conv-1")
        mock_client.conversations.create.return_value = mock_conv
        mock_client.conversations.messages.create.return_value = (
            _make_mock_async_stream(_make_assistant_message("Resumed shared!"))
        )

        tools = FakeAgentTools()
        msg = make_platform_message(room_id="room-1")
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        mock_client.agents.retrieve.assert_called_once_with("pre-existing-agent")
        mock_client.agents.create.assert_not_called()
        assert adapter._shared_agent_id == "pre-existing-agent"


# ──────────────────────────────────────────────────────────────────────
# MCP tool attachment
# ──────────────────────────────────────────────────────────────────────


class TestMCPToolAttachment:
    @pytest.mark.asyncio
    async def test_mcp_tools_attached_on_agent_creation(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = ["tool-1", "tool-2", "tool-3"]

        mock_agent = _make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent

        agent_id = await adapter._create_agent()

        assert agent_id == "new-agent"
        assert mock_client.agents.tools.attach.call_count == 3
        attach_calls = mock_client.agents.tools.attach.call_args_list
        for i, call in enumerate(attach_calls):
            assert call.kwargs["agent_id"] == "new-agent"
            assert call.kwargs["tool_id"] == f"tool-{i + 1}"

    @pytest.mark.asyncio
    async def test_verify_and_reattach_missing_tools(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp_tool_ids = ["t1", "t2", "t3"]

        # Agent has only t1 attached
        existing_tool = MagicMock()
        existing_tool.id = "t1"
        mock_client.agents.tools.list.return_value = _make_mock_tool_page(existing_tool)

        await adapter._verify_mcp_tools_attached("agent-1")

        # Should re-attach t2 and t3
        assert mock_client.agents.tools.attach.call_count == 2

    @pytest.mark.asyncio
    async def test_attach_failure_logs_warning(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._mcp_tool_ids = ["t1"]

        mock_client.agents.tools.attach.side_effect = Exception("attach failed")
        mock_agent = _make_mock_agent()
        mock_client.agents.create.return_value = mock_agent

        # Should not raise — just log warning
        agent_id = await adapter._create_agent()
        assert agent_id == mock_agent.id


# ──────────────────────────────────────────────────────────────────────
# Execution reporting (observation only)
# ──────────────────────────────────────────────────────────────────────


class TestExecutionReporting:
    @pytest.mark.asyncio
    async def test_reports_non_silent_tool_calls(self) -> None:
        config = LettaAdapterConfig(enable_execution_reporting=True)
        adapter = LettaAdapter(config=config)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_tool_call_message("band_lookup_peers", "{}"),
            _make_tool_return_message("band_lookup_peers", '{"peers": []}'),
            _make_assistant_message("Done"),
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

    @pytest.mark.asyncio
    async def test_silent_tools_not_reported(self) -> None:
        config = LettaAdapterConfig(enable_execution_reporting=True)
        adapter = LettaAdapter(config=config)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_tool_call_message("band_send_message"),
            _make_tool_return_message("band_send_message"),
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        tool_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in ("tool_call", "tool_result")
        ]
        assert len(tool_events) == 0


# ──────────────────────────────────────────────────────────────────────
# on_cleanup
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterOnCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_room_state(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._rooms
        # Memory consolidation was attempted
        mock_client.agents.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client

        # Room doesn't exist
        await adapter.on_cleanup("nonexistent")
        mock_client.agents.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_twice(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")  # Should not raise

        assert "room-1" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_cleanup_multi_room(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")
        adapter._rooms["room-2"] = _RoomContext(agent_id="agent-2")

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._rooms
        assert "room-2" in adapter._rooms

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self) -> None:
        adapter = LettaAdapter()
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        # No client — should not raise
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_cleanup_before_started(self) -> None:
        adapter = LettaAdapter()
        # Completely uninitialized — no client, no rooms
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._rooms


# ──────────────────────────────────────────────────────────────────────
# Instruction block update
# ──────────────────────────────────────────────────────────────────────


class TestInstructionBlockUpdate:
    @pytest.mark.asyncio
    async def test_updates_persona_block(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Test system prompt"
        mock_client = AsyncMock()
        adapter._client = mock_client

        await adapter._update_instruction_block("agent-1", "room-1")

        mock_client.agents.blocks.update.assert_called_once_with(
            "persona",
            agent_id="agent-1",
            value=_LETTA_TOOL_ENFORCEMENT + "Test system prompt",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_next_label(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Test prompt"
        mock_client = AsyncMock()
        adapter._client = mock_client

        # First label fails, second succeeds
        mock_client.agents.blocks.update.side_effect = [
            Exception("not found"),
            None,
            None,
        ]

        await adapter._update_instruction_block("agent-1", "room-1")

        assert mock_client.agents.blocks.update.call_count == 2
        second_call = mock_client.agents.blocks.update.call_args_list[1]
        assert second_call.args[0] == "custom_instructions"

    @pytest.mark.asyncio
    async def test_creates_persona_when_all_labels_fail(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Test prompt"
        mock_client = AsyncMock()
        adapter._client = mock_client

        mock_client.agents.blocks.update.side_effect = Exception("not found")
        mock_block = MagicMock()
        mock_block.id = "block-1"
        mock_client.blocks.create.return_value = mock_block

        await adapter._update_instruction_block("agent-1", "room-1")

        mock_client.blocks.create.assert_called_once()
        assert mock_client.blocks.create.call_args.kwargs["label"] == "persona"
        mock_client.agents.blocks.attach.assert_called_once_with(
            "block-1",
            agent_id="agent-1",
        )


# ──────────────────────────────────────────────────────────────────────
# Rejoin context
# ──────────────────────────────────────────────────────────────────────


class TestRejoinContext:
    @pytest.mark.asyncio
    async def test_rejoin_injects_time_away(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []

        last_time = datetime.now(timezone.utc) - timedelta(hours=2)
        adapter._rooms["room-1"] = _RoomContext(
            agent_id="agent-1",
            last_interaction=last_time,
            summary="Discussed project plan",
        )

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("I'm back!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        content = call_kwargs["messages"][0]["content"]
        assert "rejoined" in content
        assert "2h" in content
        assert "Discussed project plan" in content


# ──────────────────────────────────────────────────────────────────────
# Task events
# ──────────────────────────────────────────────────────────────────────


class TestTaskEvents:
    @pytest.mark.asyncio
    async def test_emits_task_event_on_agent_creation(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []

        mock_agent = _make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("Hi!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1
        metadata = task_events[0]["metadata"]
        assert metadata["letta_agent_id"] == "new-agent"
        assert metadata["letta_room_id"] == "room-1"

    @pytest.mark.asyncio
    async def test_task_events_disabled(self) -> None:
        config = LettaAdapterConfig(enable_task_events=False)
        adapter = LettaAdapter(config=config)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []

        mock_agent = _make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("Hi!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 0

    @pytest.mark.asyncio
    async def test_shared_mode_emits_conversation_id(self) -> None:
        config = LettaAdapterConfig(mode="shared")
        adapter = LettaAdapter(config=config)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []

        mock_agent = _make_mock_agent("shared-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_conv = _make_mock_conversation("conv-123")
        mock_client.conversations.create.return_value = mock_conv
        mock_client.conversations.messages.create.return_value = (
            _make_mock_async_stream(_make_assistant_message("Hi!"))
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1
        assert task_events[0]["metadata"]["letta_conversation_id"] == "conv-123"


# ──────────────────────────────────────────────────────────────────────
# Memory consolidation
# ──────────────────────────────────────────────────────────────────────


class TestMemoryConsolidation:
    @pytest.mark.asyncio
    async def test_consolidation_on_cleanup(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.messages.create.assert_called_once()
        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        assert "Consolidate" in call_kwargs["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_consolidation_failure_does_not_propagate(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.side_effect = Exception("API error")

        # Should not raise
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._rooms


# ──────────────────────────────────────────────────────────────────────
# Static helpers
# ──────────────────────────────────────────────────────────────────────


class TestFormatTimeAgo:
    def test_seconds(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert "30s" == LettaAdapter._format_time_ago(dt)

    def test_minutes(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert "5m" == LettaAdapter._format_time_ago(dt)

    def test_hours(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        assert "3h" == LettaAdapter._format_time_ago(dt)

    def test_one_hour(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        assert "1 hour" == LettaAdapter._format_time_ago(dt)

    def test_days(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(days=5)
        assert "5d" == LettaAdapter._format_time_ago(dt)

    def test_one_day(self) -> None:
        dt = datetime.now(timezone.utc) - timedelta(days=1)
        assert "1 day" == LettaAdapter._format_time_ago(dt)

    def test_naive_datetime_treated_as_utc(self) -> None:
        dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        result = LettaAdapter._format_time_ago(dt)
        assert "10m" == result


class TestExtractSummary:
    def test_first_sentence(self) -> None:
        parts = ["Hello there. This is more text."]
        assert "Hello there." == LettaAdapter._extract_summary(parts)

    def test_truncation(self) -> None:
        parts = ["A" * 200]
        result = LettaAdapter._extract_summary(parts, max_length=50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")

    def test_empty(self) -> None:
        assert "" == LettaAdapter._extract_summary([])

    def test_short_text(self) -> None:
        parts = ["Short"]
        assert "Short" == LettaAdapter._extract_summary(parts)

    def test_multiple_parts(self) -> None:
        parts = ["First part.", "Second part."]
        assert "First part." == LettaAdapter._extract_summary(parts)


# ──────────────────────────────────────────────────────────────────────
# Auto-relay mode (no MCP server)
# ──────────────────────────────────────────────────────────────────────


class TestAutoRelayMode:
    """With ``mcp_server_url=None`` the adapter registers no MCP server and relays
    the model's plain-text reply to the room itself."""

    @pytest.mark.asyncio
    async def test_on_started_skips_mcp_registration_when_disabled(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(mcp_server_url=None))
        assert adapter._mcp_enabled is False

        mock_client = AsyncMock()
        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            await adapter.on_started("TestBot", "A test bot")

        mock_client.mcp_servers.create.assert_not_called()
        mock_client.mcp_servers.list.assert_not_called()
        assert adapter._mcp_tool_ids == []

    def test_relay_preamble_when_mcp_disabled(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(mcp_server_url=None))
        assert adapter._persona_preamble() == _LETTA_RELAY_NOTE

    def test_enforcement_preamble_when_mcp_enabled(self) -> None:
        # Default config has a non-empty mcp_server_url → MCP enabled.
        adapter = LettaAdapter()
        assert adapter._mcp_enabled is True
        assert adapter._persona_preamble() == _LETTA_TOOL_ENFORCEMENT

    @pytest.mark.asyncio
    async def test_reply_relayed_without_mcp(self) -> None:
        """The model's assistant text is relayed to the room when there are no
        platform tools (the model can't call band_send_message)."""
        adapter = LettaAdapter(config=LettaAdapterConfig(mcp_server_url=None))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("Hello there!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "Hello there!"

    @pytest.mark.asyncio
    async def test_warns_when_capability_requested_in_auto_relay(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Requesting memory/contacts with no MCP server warns (tools are inert)."""
        from band.core.types import AdapterFeatures, Capability

        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp_server_url=None),
            features=AdapterFeatures(capabilities=frozenset({Capability.MEMORY})),
        )
        mock_client = AsyncMock()
        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            with caplog.at_level("WARNING"):
                await adapter.on_started("TestBot", "A test bot")

        assert any(
            "auto-relay mode" in r.message and "memory" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_capability_warning_in_auto_relay_without_features(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No capability warning when no capabilities were requested."""
        adapter = LettaAdapter(config=LettaAdapterConfig(mcp_server_url=None))
        mock_client = AsyncMock()
        mock_letta_module = MagicMock()
        mock_letta_module.AsyncLetta = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"letta_client": mock_letta_module}):
            with caplog.at_level("WARNING"):
                await adapter.on_started("TestBot", "A test bot")

        assert not any("auto-relay mode" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_verify_mcp_tools_is_noop_when_disabled(self) -> None:
        """Auto-relay mode skips the agents.tools.list round-trip on resume."""
        adapter = LettaAdapter(config=LettaAdapterConfig(mcp_server_url=None))
        mock_client = AsyncMock()
        adapter._client = mock_client

        await adapter._verify_mcp_tools_attached("agent-1")

        mock_client.agents.tools.list.assert_not_called()

    @pytest.mark.asyncio
    async def test_created_agent_uses_relay_preamble_when_disabled(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(mcp_server_url=None))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"

        mock_client.agents.create.return_value = _make_mock_agent("agent-1")

        await adapter._create_agent()

        memory_blocks = mock_client.agents.create.call_args.kwargs["memory_blocks"]
        persona = next(b for b in memory_blocks if b["label"] == "persona")
        assert persona["value"].startswith(_LETTA_RELAY_NOTE)
        assert _LETTA_TOOL_ENFORCEMENT not in persona["value"]


class TestSummaryStorage:
    @pytest.mark.asyncio
    async def test_summary_stored_after_turn(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp_tool_ids = []
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = _make_letta_response(
            _make_assistant_message("The weather is sunny. More details follow.")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        room_ctx = adapter._rooms["room-1"]
        assert room_ctx.summary == "The weather is sunny."
        assert room_ctx.last_interaction is not None
