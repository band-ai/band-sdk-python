"""Tests for BandACPServerAdapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.integrations.acp.router import AgentRouter
from thenvoi.integrations.acp.server_adapter import BandACPServerAdapter
from thenvoi.integrations.acp.types import ACPSessionState, PendingACPPrompt
from thenvoi.testing import FakeAgentTools

from .conftest import make_platform_message, make_tool_call_message


async def _wait_for_pending_prompt(
    adapter: BandACPServerAdapter, room_id: str
) -> PendingACPPrompt:
    """Wait until a pending prompt is registered for a room."""
    while True:
        pending = adapter._pending_prompts.get(room_id)
        if pending is not None:
            return pending
        await asyncio.sleep(0)


class TestBandACPServerAdapterInit:
    """Tests for BandACPServerAdapter initialization."""

    def test_init_default_values(self) -> None:
        """Should initialize with default values."""
        adapter = BandACPServerAdapter()

        assert adapter._session_to_room == {}
        assert adapter._room_to_session == {}
        assert adapter._pending_prompts == {}
        assert adapter._acp_client is None

    def test_init_creates_rest_client(self) -> None:
        """Should create AsyncRestClient."""
        adapter = BandACPServerAdapter(
            rest_url="https://api.example.com",
            api_key="my-key",
        )

        assert adapter._rest is not None

    def test_init_sets_history_converter(self) -> None:
        """Should set ACPServerHistoryConverter."""
        adapter = BandACPServerAdapter()

        assert adapter.history_converter is not None


class TestBandACPServerAdapterOnStarted:
    """Tests for BandACPServerAdapter.on_started()."""

    @pytest.mark.asyncio
    async def test_on_started_stores_agent_info(self) -> None:
        """Should store agent name and description."""
        adapter = BandACPServerAdapter()

        await adapter.on_started("Test ACP Agent", "An ACP agent for testing")

        assert adapter.agent_name == "Test ACP Agent"
        assert adapter.agent_description == "An ACP agent for testing"


class TestBandACPServerAdapterCreateSession:
    """Tests for BandACPServerAdapter.create_session()."""

    @pytest.mark.asyncio
    async def test_create_session_creates_room(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should create room via REST and map session -> room."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client

        session_id = await adapter.create_session()

        assert session_id is not None
        assert len(session_id) == 32  # uuid4().hex
        assert adapter._session_to_room[session_id] == "room-new-123"
        assert adapter._room_to_session["room-new-123"] == session_id

        mock_rest_client.agent_api_chats.create_agent_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_session_emits_context_event(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should emit task event with session mapping."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client

        session_id = await adapter.create_session()

        mock_rest_client.agent_api_events.create_agent_chat_event.assert_called_once()
        call_kwargs = (
            mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        )
        event = call_kwargs.kwargs.get("event") or call_kwargs[1].get("event")
        assert event.metadata["acp_session_id"] == session_id
        assert event.metadata["acp_room_id"] == "room-new-123"
        assert event.metadata["acp_cwd"] == "."
        assert event.metadata["acp_mcp_servers"] == []
        assert "request_options" in call_kwargs.kwargs

    @pytest.mark.asyncio
    async def test_create_session_persists_mcp_servers(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should normalize editor MCP servers into session state and history."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client

        mcp_servers = [
            {
                "type": "stdio",
                "name": "filesystem",
                "command": "mcp-filesystem",
            }
        ]
        session_id = await adapter.create_session(
            cwd="/workspace/project",
            mcp_servers=mcp_servers,
        )

        assert adapter._session_cwd[session_id] == "/workspace/project"
        assert adapter._session_mcp_servers[session_id] == mcp_servers

        call_kwargs = (
            mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        )
        event = call_kwargs.kwargs.get("event") or call_kwargs[1].get("event")
        assert event.metadata["acp_cwd"] == "/workspace/project"
        assert event.metadata["acp_mcp_servers"] == mcp_servers

    @pytest.mark.asyncio
    async def test_create_session_multiple_sessions(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should create unique sessions for each call."""
        # Return different room IDs for each call
        room_counter = [0]

        def create_room_side_effect(*args, **kwargs):
            room_counter[0] += 1
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = f"room-{room_counter[0]}"
            return mock_response

        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            side_effect=create_room_side_effect
        )

        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client

        session_1 = await adapter.create_session()
        session_2 = await adapter.create_session()

        assert session_1 != session_2
        assert adapter._session_to_room[session_1] == "room-1"
        assert adapter._session_to_room[session_2] == "room-2"


class TestBandACPServerAdapterHandlePrompt:
    """Tests for BandACPServerAdapter.handle_prompt()."""

    @pytest.mark.asyncio
    async def test_handle_prompt_sends_message(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should send message to room with mentions."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"

        # Make pending prompt complete immediately via on_message
        async def auto_complete():
            pending = await asyncio.wait_for(
                _wait_for_pending_prompt(adapter, "room-123"),
                timeout=0.5,
            )
            pending.done_event.set()

        task = asyncio.create_task(auto_complete())
        await adapter.handle_prompt("session-1", "Hello world")
        await task

        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_prompt_registers_pending(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should register pending prompt before sending."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"

        # Complete immediately
        async def auto_complete():
            pending = await asyncio.wait_for(
                _wait_for_pending_prompt(adapter, "room-123"),
                timeout=0.5,
            )
            pending.done_event.set()

        task = asyncio.create_task(auto_complete())
        await adapter.handle_prompt("session-1", "Test")
        await task

    @pytest.mark.asyncio
    async def test_handle_prompt_unknown_session_raises(self) -> None:
        """Should raise KeyError for unknown session_id."""
        adapter = BandACPServerAdapter()

        with pytest.raises(KeyError):
            await adapter.handle_prompt("unknown-session", "Hello")


class TestBandACPServerAdapterOnMessage:
    """Tests for BandACPServerAdapter.on_message()."""

    @pytest.mark.asyncio
    async def test_on_message_streams_to_acp_client(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should stream response to ACP client via session_update."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_platform_message("Hello from peer", room_id="room-123")

        # Set up pending prompt
        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        mock_acp_client.session_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_message_sets_done_on_text(
        self,
        mock_acp_client: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should set done_event on text message after the grace period."""
        monkeypatch.setattr(
            "thenvoi.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS",
            0.01,
        )
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_platform_message("Done", room_id="room-123", message_type="text")

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert not pending.done_event.is_set()
        await asyncio.sleep(0.02)
        assert pending.done_event.is_set()
        assert "room-123" not in adapter._pending_prompts

    @pytest.mark.asyncio
    async def test_on_message_waits_for_follow_up_text(
        self,
        mock_acp_client: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should debounce completion so split text replies are not truncated."""
        monkeypatch.setattr(
            "thenvoi.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS",
            0.02,
        )
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        first = make_platform_message("Part 1", room_id="room-123", message_type="text")
        second = make_platform_message(
            "Part 2", room_id="room-123", message_type="text"
        )

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            first,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )
        await asyncio.sleep(0.01)
        await adapter.on_message(
            second,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        await asyncio.sleep(0.015)
        assert not pending.done_event.is_set()
        await asyncio.sleep(0.02)
        assert pending.done_event.is_set()
        assert mock_acp_client.session_update.await_count == 2

    @pytest.mark.asyncio
    async def test_on_message_does_not_set_done_on_thought(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should not set done_event on thought message (non-terminal)."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_platform_message(
            "Thinking...", room_id="room-123", message_type="thought"
        )

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert not pending.done_event.is_set()
        assert "room-123" in adapter._pending_prompts

    @pytest.mark.asyncio
    async def test_on_message_does_not_set_done_on_tool_call(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should not set done_event on tool_call message."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_tool_call_message(name="get_weather", tool_call_id="tc-1")

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert not pending.done_event.is_set()

    @pytest.mark.asyncio
    async def test_on_message_does_not_set_done_on_tool_result(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should not set done_event on tool_result message."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_platform_message(
            "72F sunny", room_id="room-123", message_type="tool_result"
        )

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert not pending.done_event.is_set()

    @pytest.mark.asyncio
    async def test_on_message_uses_event_converter(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should use EventConverter for rich message type forwarding."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_platform_message(
            "Thinking about it...", room_id="room-123", message_type="thought"
        )

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Verify the chunk sent has thought type
        call_args = mock_acp_client.session_update.call_args
        chunk = call_args.kwargs.get("update") or call_args[1].get("update")
        assert getattr(chunk, "session_update", None) == "agent_thought_chunk"

    @pytest.mark.asyncio
    async def test_on_message_tool_call_forwarded(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should forward tool_call as ToolCallStart via EventConverter."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        tools = FakeAgentTools()
        msg = make_tool_call_message(
            name="search", args={"q": "test"}, tool_call_id="tc-99"
        )

        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        call_args = mock_acp_client.session_update.call_args
        chunk = call_args.kwargs.get("update") or call_args[1].get("update")
        assert getattr(chunk, "session_update", None) == "tool_call"
        assert getattr(chunk, "tool_call_id", None) == "tc-99"

    @pytest.mark.asyncio
    async def test_on_message_push_handler_called_when_no_pending(
        self, mock_acp_client: AsyncMock
    ) -> None:
        """Should call push handler when no pending prompt exists."""
        adapter = BandACPServerAdapter()
        adapter._acp_client = mock_acp_client
        adapter._room_to_session["room-123"] = "session-1"
        tools = FakeAgentTools()
        msg = make_platform_message("Activity", room_id="room-123")

        mock_push = AsyncMock()
        mock_push.handle_push_event = AsyncMock()
        adapter._push_handler = mock_push

        # No pending prompt
        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        mock_push.handle_push_event.assert_called_once_with(msg, "room-123")

    @pytest.mark.asyncio
    async def test_on_message_rehydrates_on_bootstrap(self) -> None:
        """Should rehydrate session state on bootstrap."""
        adapter = BandACPServerAdapter()
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        history = ACPSessionState(
            session_to_room={"session-a": "room-a", "session-b": "room-b"},
        )

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert adapter._session_to_room == {
            "session-a": "room-a",
            "session-b": "room-b",
        }
        assert adapter._room_to_session == {
            "room-a": "session-a",
            "room-b": "session-b",
        }

    @pytest.mark.asyncio
    async def test_on_message_no_pending_no_crash(self) -> None:
        """Should handle messages without pending prompt gracefully."""
        adapter = BandACPServerAdapter()
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        # No pending prompt, no ACP client - should not crash
        await adapter.on_message(
            msg,
            tools,
            ACPSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )


class TestBandACPServerAdapterCleanup:
    """Tests for cleanup methods."""

    @pytest.mark.asyncio
    async def test_on_cleanup_removes_pending_prompts(self) -> None:
        """Should remove pending prompt for room."""
        adapter = BandACPServerAdapter()
        adapter._pending_prompts["room-123"] = PendingACPPrompt(session_id="session-1")

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._pending_prompts

    @pytest.mark.asyncio
    async def test_on_cleanup_removes_session_mappings(self) -> None:
        """Should remove all session state for a room."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-123"
        adapter._room_to_session["room-123"] = "session-1"
        adapter._session_modes["session-1"] = "code"
        adapter._pending_prompts["room-123"] = PendingACPPrompt(session_id="session-1")

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_to_session
        assert "session-1" not in adapter._session_to_room
        assert "session-1" not in adapter._session_modes
        assert "room-123" not in adapter._pending_prompts

    @pytest.mark.asyncio
    async def test_on_cleanup_idempotent(self) -> None:
        """Should handle cleanup of non-existent room."""
        adapter = BandACPServerAdapter()

        # Should not raise
        await adapter.on_cleanup("nonexistent-room")

    @pytest.mark.asyncio
    async def test_on_cleanup_twice(self) -> None:
        """Should handle cleanup called twice for same room."""
        adapter = BandACPServerAdapter()
        adapter._pending_prompts["room-123"] = PendingACPPrompt(session_id="session-1")
        adapter._session_to_room["session-1"] = "room-123"
        adapter._room_to_session["room-123"] = "session-1"

        await adapter.on_cleanup("room-123")
        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._pending_prompts
        assert "session-1" not in adapter._session_to_room
        assert "room-123" not in adapter._room_to_session

    @pytest.mark.asyncio
    async def test_on_cleanup_with_pending_prompts(self) -> None:
        """Should clean up even with active pending prompts."""
        adapter = BandACPServerAdapter()
        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._pending_prompts
        assert pending.done_event.is_set()


class TestBandACPServerAdapterCancelPrompt:
    """Tests for cancel_prompt()."""

    @pytest.mark.asyncio
    async def test_cancel_prompt_sets_done_event(self) -> None:
        """Should set done_event to unblock handle_prompt."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-123"
        pending = PendingACPPrompt(session_id="session-1")
        adapter._pending_prompts["room-123"] = pending

        await adapter.cancel_prompt("session-1")

        assert pending.done_event.is_set()
        assert "room-123" not in adapter._pending_prompts

    @pytest.mark.asyncio
    async def test_cancel_prompt_unknown_session(self) -> None:
        """Should handle unknown session_id gracefully."""
        adapter = BandACPServerAdapter()

        # Should not raise
        await adapter.cancel_prompt("unknown-session")

    @pytest.mark.asyncio
    async def test_cancel_prompt_no_pending(self) -> None:
        """Should handle cancel when no pending prompt exists."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-123"

        # Should not raise
        await adapter.cancel_prompt("session-1")


class TestBandACPServerAdapterRehydration:
    """Tests for session rehydration."""

    def test_rehydrate_restores_session_mapping(self) -> None:
        """Should restore session -> room mappings."""
        adapter = BandACPServerAdapter()

        history = ACPSessionState(
            session_to_room={"session-1": "room-1", "session-2": "room-2"},
            session_cwd={"session-1": "/workspace/project"},
            session_mcp_servers={
                "session-1": [
                    {
                        "type": "stdio",
                        "name": "filesystem",
                        "command": "mcp-filesystem",
                    }
                ]
            },
        )

        adapter._rehydrate(history)

        assert adapter._session_to_room == {
            "session-1": "room-1",
            "session-2": "room-2",
        }
        assert adapter._room_to_session == {
            "room-1": "session-1",
            "room-2": "session-2",
        }
        assert adapter._session_cwd["session-1"] == "/workspace/project"
        assert adapter._session_mcp_servers["session-1"] == [
            {
                "type": "stdio",
                "name": "filesystem",
                "command": "mcp-filesystem",
            }
        ]

    def test_rehydrate_does_not_overwrite_existing(self) -> None:
        """Should not overwrite existing session mappings."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["session-1"] = "current-room"
        adapter._room_to_session["current-room"] = "session-1"

        history = ACPSessionState(
            session_to_room={"session-1": "old-room"},
        )

        adapter._rehydrate(history)

        assert adapter._session_to_room["session-1"] == "current-room"

    def test_rehydrate_merges_with_existing(self) -> None:
        """Should merge new mappings with existing ones."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["existing"] = "existing-room"
        adapter._room_to_session["existing-room"] = "existing"

        history = ACPSessionState(
            session_to_room={"new-session": "new-room"},
        )

        adapter._rehydrate(history)

        assert adapter._session_to_room["existing"] == "existing-room"
        assert adapter._session_to_room["new-session"] == "new-room"


class TestBandACPServerAdapterRouting:
    """Tests for router-based prompt routing."""

    @pytest.mark.asyncio
    async def test_handle_prompt_with_router_single_peer(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should mention only target peer when router resolves."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"

        # Set up participants
        peer_a = MagicMock()
        peer_a.id = "peer-a-id"
        peer_a.name = "codex"
        peer_a.type = "bot"
        peer_b = MagicMock()
        peer_b.id = "peer-b-id"
        peer_b.name = "claude"
        peer_b.type = "bot"
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value.data = [
            peer_a,
            peer_b,
        ]

        router = AgentRouter(slash_commands={"codex": "codex"})
        adapter.set_router(router)

        async def auto_complete():
            pending = await asyncio.wait_for(
                _wait_for_pending_prompt(adapter, "room-123"),
                timeout=0.5,
            )
            pending.done_event.set()

        task = asyncio.create_task(auto_complete())
        await adapter.handle_prompt("session-1", "/codex fix bug")
        await task

        # Verify only codex was mentioned
        call_kwargs = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_kwargs.kwargs.get("message") or call_kwargs[1].get("message")
        assert "@codex" in message.content
        assert "@claude" not in message.content

    @pytest.mark.asyncio
    async def test_handle_prompt_without_router(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should mention all peers when no router is set."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"

        async def auto_complete():
            pending = await asyncio.wait_for(
                _wait_for_pending_prompt(adapter, "room-123"),
                timeout=0.5,
            )
            pending.done_event.set()

        task = asyncio.create_task(auto_complete())
        await adapter.handle_prompt("session-1", "Hello")
        await task

        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_prompt_includes_session_context(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should prepend editor cwd and MCP server info to forwarded prompts."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"
        adapter._session_cwd["session-1"] = "/workspace/project"
        adapter._session_mcp_servers["session-1"] = [
            {
                "type": "stdio",
                "name": "filesystem",
                "command": "mcp-filesystem",
            }
        ]

        async def auto_complete() -> None:
            pending = await asyncio.wait_for(
                _wait_for_pending_prompt(adapter, "room-123"),
                timeout=0.5,
            )
            pending.done_event.set()

        task = asyncio.create_task(auto_complete())
        await adapter.handle_prompt("session-1", "Check the repo")
        await task

        call_kwargs = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_kwargs.kwargs.get("message") or call_kwargs[1].get("message")
        assert "[ACP Session Context]" in message.content
        assert "Editor cwd: /workspace/project" in message.content
        assert "filesystem (stdio, command=mcp-filesystem)" in message.content
        assert "Check the repo" in message.content


class TestBandACPServerAdapterPublicAccessors:
    """Tests for public accessor methods added for encapsulation."""

    def test_has_session(self) -> None:
        """Should return True for known sessions."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-1"

        assert adapter.has_session("session-1") is True
        assert adapter.has_session("unknown") is False

    def test_get_session_ids(self) -> None:
        """Should iterate over all session IDs."""
        adapter = BandACPServerAdapter()
        adapter._session_to_room["s1"] = "r1"
        adapter._session_to_room["s2"] = "r2"

        assert set(adapter.get_session_ids()) == {"s1", "s2"}

    def test_set_and_get_session_mode(self) -> None:
        """Should store session mode."""
        adapter = BandACPServerAdapter()
        adapter.set_session_mode("session-1", "code")

        assert adapter._session_modes["session-1"] == "code"

    def test_set_session_model_logs(self) -> None:
        """Should store the selected model."""
        adapter = BandACPServerAdapter()
        adapter.set_session_model("session-1", "gpt-4")
        assert adapter._session_models["session-1"] == "gpt-4"

    def test_get_session_for_room(self) -> None:
        """Should return session_id for known rooms, None otherwise."""
        adapter = BandACPServerAdapter()
        adapter._room_to_session["room-1"] = "session-1"

        assert adapter.get_session_for_room("room-1") == "session-1"
        assert adapter.get_session_for_room("unknown") is None

    def test_get_acp_client_none_by_default(self) -> None:
        """Should return None when no client is connected."""
        adapter = BandACPServerAdapter()
        assert adapter.get_acp_client() is None

    def test_get_acp_client_after_set(self) -> None:
        """Should return the client after it's been set."""
        adapter = BandACPServerAdapter()
        mock_client = MagicMock()
        adapter.set_acp_client(mock_client)

        assert adapter.get_acp_client() is mock_client

    @pytest.mark.asyncio
    async def test_verify_credentials_success(self) -> None:
        """Should return True when identity endpoint succeeds."""
        adapter = BandACPServerAdapter()
        adapter._rest = MagicMock()
        adapter._rest.agent_api_identity.get_agent_me = AsyncMock()

        assert await adapter.verify_credentials() is True

    @pytest.mark.asyncio
    async def test_verify_credentials_failure(self) -> None:
        """Should return False when identity endpoint fails."""
        adapter = BandACPServerAdapter()
        adapter._rest = MagicMock()
        adapter._rest.agent_api_identity.get_agent_me = AsyncMock(
            side_effect=Exception("401 Unauthorized")
        )

        assert await adapter.verify_credentials() is False


class TestBandACPServerAdapterTimeout:
    """Tests for prompt timeout behavior."""

    @pytest.mark.asyncio
    async def test_handle_prompt_timeout_raises(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise TimeoutError when peer never responds."""
        monkeypatch.setattr(
            "thenvoi.integrations.acp.server_adapter._PROMPT_TIMEOUT_SECONDS",
            0.05,
        )

        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"

        with pytest.raises(asyncio.TimeoutError):
            await adapter.handle_prompt("session-1", "Hello")

        # Verify pending prompt was cleaned up
        assert "room-123" not in adapter._pending_prompts

    @pytest.mark.asyncio
    async def test_handle_prompt_unknown_session_descriptive_error(self) -> None:
        """Should raise KeyError with descriptive message."""
        adapter = BandACPServerAdapter()

        with pytest.raises(KeyError, match="Unknown ACP session"):
            await adapter.handle_prompt("nonexistent", "Hello")


class TestBandACPServerAdapterCreateSessionRollback:
    """Tests for create_session atomicity."""

    @pytest.mark.asyncio
    async def test_create_session_rolls_back_on_event_failure(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should roll back mappings if emit_session_event fails."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        mock_rest_client.agent_api_events.create_agent_chat_event = AsyncMock(
            side_effect=Exception("Network error")
        )

        with pytest.raises(Exception, match="Network error"):
            await adapter.create_session()

        # Mappings should be rolled back
        assert adapter._session_to_room == {}
        assert adapter._room_to_session == {}
        assert adapter._session_cwd == {}
        assert adapter._session_mcp_servers == {}

    @pytest.mark.asyncio
    async def test_create_session_enforces_max_sessions(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should reject new sessions once the configured limit is reached."""
        monkeypatch.setattr(
            "thenvoi.integrations.acp.server_adapter._MAX_SESSIONS",
            1,
        )
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["existing-session"] = "room-existing"

        with pytest.raises(RuntimeError, match="Maximum sessions"):
            await adapter.create_session()

    @pytest.mark.asyncio
    async def test_handle_prompt_cleans_up_pending_on_send_failure(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should drop the pending prompt immediately when message creation fails."""
        adapter = BandACPServerAdapter()
        adapter._rest = mock_rest_client
        adapter._session_to_room["session-1"] = "room-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=RuntimeError("send failed")
        )

        with pytest.raises(RuntimeError, match="send failed"):
            await adapter.handle_prompt("session-1", "Hello")

        assert "room-123" not in adapter._pending_prompts
