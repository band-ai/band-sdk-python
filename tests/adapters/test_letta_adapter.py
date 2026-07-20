"""Tests for LettaAdapter.

Message-path and agent-lifecycle coverage. MCP wiring/lifecycle tests live
in ``test_letta_mcp.py``; the shared mock factories in ``lettakit.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
    _RoomContext,
)
from band.converters.letta import LettaSessionState
from band.testing import FakeAgentTools
from tests.adapters.lettakit import (
    default_enforcement,
    make_assistant_message,
    make_letta_response,
    make_mock_agent,
    make_mock_async_stream,
    make_mock_conversation,
    make_mock_tool_page,
    make_platform_message,
    make_tool_call_message,
    make_tool_return_message,
)


# ──────────────────────────────────────────────────────────────────────
# Initialization
# ──────────────────────────────────────────────────────────────────────


class TestLettaAdapterInit:
    # Default values are not asserted here: that would only restate the
    # dataclass definition, and the frozen-config conformance suite already
    # pins ``LettaAdapter().config == LettaAdapterConfig()``.

    def test_custom_config_reaches_adapter(self) -> None:
        config = LettaAdapterConfig(
            base_url="http://custom:8283",
            provider_key="sk-test",
            mode="shared",
            mcp=LettaMCPConfig(mode="external", server_url="http://mcp:9000/sse"),
            enable_execution_reporting=True,
        )
        adapter = LettaAdapter(config=config)
        assert adapter.config is config


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
        adapter._mcp.tool_ids = ["t1", "t2"]
        adapter._mcp.server_id = "mcp-server-1"
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_basic_message_creates_agent_and_sends(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        mock_agent = make_mock_agent("agent-1")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hello!")
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

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'll help you!")
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

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("band_send_message"),
            make_tool_return_message("band_send_message"),
            make_assistant_message("Done!"),
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
            return make_letta_response()

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

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Got it.")
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

        mock_client.agents.retrieve.return_value = make_mock_agent("history-agent")
        mock_client.agents.tools.list.return_value = make_mock_tool_page()
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Resumed!")
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
        adapter._mcp.tool_ids = ["t1", "t2"]
        adapter._mcp.server_id = "mcp-server-1"
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_shared_mode_creates_agent_and_conversation(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = shared_adapter

        mock_agent = make_mock_agent("shared-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_conv = make_mock_conversation("conv-room1")
        mock_client.conversations.create.return_value = mock_conv
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Hi from shared!")
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

        mock_conv2 = make_mock_conversation("conv-room2")
        mock_client.conversations.create.return_value = mock_conv2
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Hi room 2!")
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

        mock_client.agents.retrieve.return_value = make_mock_agent("pre-existing-agent")
        mock_client.agents.tools.list.return_value = make_mock_tool_page()
        mock_conv = make_mock_conversation("conv-1")
        mock_client.conversations.create.return_value = mock_conv
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Resumed shared!")
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

    @pytest.mark.asyncio
    async def test_shared_mode_resumes_persisted_conversation(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """A restart with a persisted conversation reuses it — a fresh one
        would silently drop the room's conversational context."""
        adapter, mock_client = shared_adapter
        adapter._shared_agent_id = "shared-agent"

        mock_client.conversations.retrieve.return_value = make_mock_conversation(
            "conv-persisted", agent_id="shared-agent"
        )
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Still here!")
        )

        history = LettaSessionState(
            agent_id="shared-agent",
            conversation_id="conv-persisted",
            replay_messages=["[Alice]: earlier"],
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        mock_client.conversations.retrieve.assert_awaited_once_with("conv-persisted")
        mock_client.conversations.create.assert_not_called()
        assert adapter._rooms["room-1"].conversation_id == "conv-persisted"
        # A resumed conversation already has its context — no seeding.
        content = mock_client.conversations.messages.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "[Alice]: earlier" not in content

    @pytest.mark.asyncio
    async def test_shared_mode_rejects_foreign_conversation(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """A persisted conversation must belong to the current shared agent."""
        adapter, mock_client = shared_adapter
        adapter._shared_agent_id = "shared-agent"

        mock_client.conversations.retrieve.return_value = make_mock_conversation(
            "conv-other", agent_id="other-agent"
        )
        mock_client.conversations.create.return_value = make_mock_conversation(
            "conv-fresh", agent_id="shared-agent"
        )
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Fresh thread")
        )

        history = LettaSessionState(
            agent_id="shared-agent",
            conversation_id="conv-other",
        )
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            FakeAgentTools(),
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        mock_client.conversations.create.assert_awaited_once_with(
            agent_id="shared-agent"
        )
        assert adapter._rooms["room-1"].conversation_id == "conv-fresh"

    @pytest.mark.asyncio
    async def test_shared_mode_seeds_fresh_conversation_from_history(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """A dead persisted conversation falls back to create + seed."""
        adapter, mock_client = shared_adapter
        adapter._shared_agent_id = "shared-agent"

        mock_client.conversations.retrieve.side_effect = Exception("gone")
        mock_client.conversations.create.return_value = make_mock_conversation(
            "conv-fresh"
        )
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Rebuilt!")
        )

        history = LettaSessionState(
            agent_id="shared-agent",
            conversation_id="conv-dead",
            replay_messages=["[Alice]: the secret is kumquat"],
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert adapter._rooms["room-1"].conversation_id == "conv-fresh"
        content = mock_client.conversations.messages.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "[Alice]: the secret is kumquat" in content

    @pytest.mark.asyncio
    async def test_shared_mode_injects_room_id_per_message(
        self, shared_adapter: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """Self-host + shared: the (shared) persona cannot carry a room id, so
        every message reminds the agent which room_id to pass to tools."""
        adapter, mock_client = shared_adapter
        adapter._shared_agent_id = "shared-agent"
        adapter._rooms["room-42"] = _RoomContext(
            agent_id="shared-agent", conversation_id="conv-1"
        )

        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Hi!")
        )

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(room_id="room-42"),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-42",
        )

        content = mock_client.conversations.messages.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "Current room_id: room-42" in content


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
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("band_lookup_peers", "{}"),
            make_tool_return_message("band_lookup_peers", '{"peers": []}'),
            make_assistant_message("Done"),
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
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("band_send_message"),
            make_tool_return_message("band_send_message"),
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

        # Default config self-hosts the MCP server in per_room mode, so the
        # enforcement carries the room id the tool schemas require.
        mock_client.agents.blocks.update.assert_called_once_with(
            "persona",
            agent_id="agent-1",
            value=default_enforcement(room_id="room-1") + "Test system prompt",
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
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"

        last_time = datetime.now(timezone.utc) - timedelta(hours=2)
        adapter._rooms["room-1"] = _RoomContext(
            agent_id="agent-1",
            last_interaction=last_time,
            summary="Discussed project plan",
        )

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'm back!")
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
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"

        mock_agent = make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hi!")
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
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"

        mock_agent = make_mock_agent("new-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hi!")
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
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"

        mock_agent = make_mock_agent("shared-agent")
        mock_client.agents.create.return_value = mock_agent
        mock_conv = make_mock_conversation("conv-123")
        mock_client.conversations.create.return_value = mock_conv
        mock_client.conversations.messages.create.return_value = make_mock_async_stream(
            make_assistant_message("Hi!")
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
    def test_short_text_kept_whole(self) -> None:
        parts = ["Hello there. This is more text."]
        assert "Hello there. This is more text." == LettaAdapter._extract_summary(parts)

    def test_truncates_on_word_boundary(self) -> None:
        parts = ["alpha beta " * 30]
        result = LettaAdapter._extract_summary(parts, max_length=50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")
        assert not result.removesuffix("...").endswith("alph")  # no mid-word cut

    def test_no_sentence_heuristics(self) -> None:
        """Decimals must not be mistaken for sentence ends."""
        parts = ["pi is 3.14 and e is 2.71"]
        assert "pi is 3.14 and e is 2.71" == LettaAdapter._extract_summary(parts)

    def test_empty(self) -> None:
        assert "" == LettaAdapter._extract_summary([])

    def test_multiple_parts_joined(self) -> None:
        parts = ["First part.", "Second part."]
        assert "First part. Second part." == LettaAdapter._extract_summary(parts)


# ──────────────────────────────────────────────────────────────────────
# Summary storage
# ──────────────────────────────────────────────────────────────────────


class TestSummaryStorage:
    @pytest.mark.asyncio
    async def test_summary_stored_after_turn(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("The weather is sunny. More details follow.")
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
        assert room_ctx.summary == "The weather is sunny. More details follow."
        assert room_ctx.last_interaction is not None


# ──────────────────────────────────────────────────────────────────────
# Send-tool name resolution (derived from discovered MCP tools)
# ──────────────────────────────────────────────────────────────────────


class TestSendToolResolution:
    def test_resolves_external_band_mcp_names(self) -> None:
        adapter = LettaAdapter()
        adapter._mcp.resolve_send_tools(
            ["create_agent_chat_message", "create_agent_chat_event", "health_check"]
        )
        assert adapter._mcp.send_message_tool == "create_agent_chat_message"
        assert adapter._mcp.send_event_tool == "create_agent_chat_event"
        assert adapter._mcp.silent_reporting_tools == {
            "create_agent_chat_message",
            "create_agent_chat_event",
        }

    def test_prefers_band_names_when_both_present(self) -> None:
        adapter = LettaAdapter()
        adapter._mcp.resolve_send_tools(
            ["band_send_message", "create_agent_chat_message", "band_send_event"]
        )
        assert adapter._mcp.send_message_tool == "band_send_message"
        assert adapter._mcp.send_event_tool == "band_send_event"

    def test_falls_back_to_band_names_when_none_discovered(self) -> None:
        adapter = LettaAdapter()
        adapter._mcp.resolve_send_tools(["some_unrelated_tool"])
        assert adapter._mcp.send_message_tool == "band_send_message"
        assert adapter._mcp.send_event_tool == "band_send_event"

    def test_enforcement_prompt_uses_resolved_names(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Base prompt"
        adapter._mcp.resolve_send_tools(
            ["create_agent_chat_message", "create_agent_chat_event"]
        )
        text = adapter._instruction_text("room-1")
        assert "create_agent_chat_message" in text
        assert "band_send_message" not in text

    @pytest.mark.asyncio
    async def test_relay_detection_uses_resolved_name(self) -> None:
        """A send via the resolved (external) tool name suppresses auto-relay."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.server_id = "mcp-server-1"
        adapter._mcp.resolve_send_tools(
            ["create_agent_chat_message", "create_agent_chat_event"]
        )
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("create_agent_chat_message"),
            make_assistant_message("Done!"),
        )

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 0


# ──────────────────────────────────────────────────────────────────────
# Auto-relay knob
# ──────────────────────────────────────────────────────────────────────


class TestAutoRelayDisabled:
    @pytest.mark.asyncio
    async def test_disabled_relay_fails_loud_instead_of_sending(self) -> None:
        """With auto_relay off, an unused MCP send path surfaces as an error
        event and the assistant text is dropped — nothing is silently relayed."""
        adapter = LettaAdapter(config=LettaAdapterConfig(auto_relay=False))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'll help you!")
        )

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 0
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert "band_send_message" in error_events[0]["content"]

    @pytest.mark.asyncio
    async def test_disabled_relay_quiet_when_send_tool_used(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(auto_relay=False))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("band_send_message"),
            make_assistant_message("Done!"),
        )

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 0
        assert not [e for e in tools.events_sent if e["message_type"] == "error"]


# ──────────────────────────────────────────────────────────────────────
# Cold-boot history seeding
# ──────────────────────────────────────────────────────────────────────


class TestColdBootSeeding:
    @pytest.fixture
    def adapter_with_client(self) -> tuple[LettaAdapter, AsyncMock]:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"
        adapter._mcp.server_id = "mcp-server-1"
        adapter._mcp.tool_ids = []
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_new_agent_in_room_with_history_is_seeded(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        mock_client.agents.create.return_value = make_mock_agent("fresh-agent")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hi again!")
        )

        history = LettaSessionState(
            replay_messages=["[Alice]: The secret word is kumquat.", "[Bot]: Noted!"]
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(content="what was the secret word?"),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        content = mock_client.agents.messages.create.call_args.kwargs["messages"][0][
            "content"
        ]
        assert "joining an ongoing conversation" in content
        assert "[Alice]: The secret word is kumquat." in content
        assert "what was the secret word?" in content
        # Seed is delivered exactly once
        assert adapter._rooms["room-1"].pending_seed == []

    @pytest.mark.asyncio
    async def test_failed_first_turn_preserves_pending_seed(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """Cold-boot history must survive a failed first turn — clearing the
        seed at compose time would drop replay context forever."""
        adapter, mock_client = adapter_with_client
        mock_client.agents.create.return_value = make_mock_agent("fresh-agent")
        mock_client.agents.messages.create.side_effect = TimeoutError("slow letta")

        history = LettaSessionState(
            replay_messages=["[Alice]: The secret word is kumquat."]
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(content="what was the secret word?"),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert adapter._rooms["room-1"].pending_seed == [
            "[Alice]: The secret word is kumquat."
        ]

    @pytest.mark.asyncio
    async def test_per_room_does_not_resume_config_agent_id(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """config.agent_id is not a per-room resume target — each room without
        persisted history must get its own Letta agent."""
        adapter, mock_client = adapter_with_client
        adapter.config.agent_id = "shared-bootstrap-id"
        mock_client.agents.create.return_value = make_mock_agent("room-agent")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hello!")
        )

        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            FakeAgentTools(),
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        mock_client.agents.retrieve.assert_not_called()
        mock_client.agents.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_second_message_is_not_reseeded(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        mock_client.agents.create.return_value = make_mock_agent("fresh-agent")
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hello!")
        )

        history = LettaSessionState(replay_messages=["[Alice]: earlier message"])
        tools = FakeAgentTools()
        for _ in range(2):
            await adapter.on_message(
                make_platform_message(),
                tools,
                history,
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        second_content = mock_client.agents.messages.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "[Alice]: earlier message" not in second_content

    @pytest.mark.asyncio
    async def test_resumed_agent_is_not_seeded(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """Resume-by-id stays the fast path — the live agent already carries
        its own context, so replaying history would duplicate it."""
        adapter, mock_client = adapter_with_client
        mock_client.agents.retrieve.return_value = make_mock_agent("live-agent")
        mock_client.agents.tools.list.return_value = make_mock_tool_page()
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Resumed!")
        )

        history = LettaSessionState(
            agent_id="live-agent",
            replay_messages=["[Alice]: earlier message"],
        )
        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        content = mock_client.agents.messages.create.call_args.kwargs["messages"][0][
            "content"
        ]
        assert "[Alice]: earlier message" not in content
        mock_client.agents.create.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# Agent-create options
# ──────────────────────────────────────────────────────────────────────


class TestAgentCreateOptions:
    @pytest.mark.asyncio
    async def test_embedding_passed_when_configured(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(embedding="openai/text-embedding-3-small")
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-1")

        create_kwargs = mock_client.agents.create.call_args.kwargs
        assert create_kwargs["embedding"] == "openai/text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_embedding_omitted_by_default(self) -> None:
        """Letta Cloud picks its own default — None must not be sent."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-1")

        assert "embedding" not in mock_client.agents.create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_per_room_persona_carries_room_id(self) -> None:
        """Self-hosted MCP schemas require room_id per call; a per_room agent
        gets it in its persona block."""
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-42")

        blocks = mock_client.agents.create.call_args.kwargs["memory_blocks"]
        persona = next(b for b in blocks if b["label"] == "persona")
        assert "room-42" in persona["value"]

    @pytest.mark.asyncio
    async def test_external_mode_persona_omits_room_id(self) -> None:
        """External band-mcp tool schemas carry no room_id argument."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        mock_client.agents.create.return_value = make_mock_agent()

        await adapter._create_agent("room-42")

        blocks = mock_client.agents.create.call_args.kwargs["memory_blocks"]
        persona = next(b for b in blocks if b["label"] == "persona")
        assert "room-42" not in persona["value"]


# ──────────────────────────────────────────────────────────────────────
# delete_agents_on_cleanup
# ──────────────────────────────────────────────────────────────────────


class TestDeleteAgentsOnCleanup:
    @pytest.mark.asyncio
    async def test_opt_in_deletes_agent_instead_of_consolidating(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(delete_agents_on_cleanup=True))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.delete.assert_awaited_once_with("agent-1")
        # No consolidation prompt when the agent is being deleted
        mock_client.agents.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_keeps_agent(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_can_skip_consolidation_on_cleanup(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(consolidate_memory_on_cleanup=False)
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.delete.assert_not_called()
        mock_client.agents.messages.create.assert_not_called()
        assert "room-1" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_delete_failure_does_not_propagate(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(delete_agents_on_cleanup=True))
        mock_client = AsyncMock()
        adapter._client = mock_client
        mock_client.agents.delete.side_effect = Exception("gone already")
        adapter._rooms["room-1"] = _RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")  # should not raise
        assert "room-1" not in adapter._rooms
