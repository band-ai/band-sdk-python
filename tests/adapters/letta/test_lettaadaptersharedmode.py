from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    RoomContext,
)
from band.converters.letta import LettaSessionState
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.lettakit import (
    make_assistant_message,
    make_mock_agent,
    make_mock_async_stream,
    make_mock_conversation,
    make_mock_tool_page,
    make_platform_message,
)


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
        adapter._rooms["room-1"] = RoomContext(
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
        adapter.config = adapter.config.model_copy(
            update={"agent_id": "pre-existing-agent"}
        )

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
        adapter._rooms["room-42"] = RoomContext(
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
        assert "Current chat_id: room-42" in content
