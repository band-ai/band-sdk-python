from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
)
from band.converters.letta import LettaSessionState
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    make_mock_agent,
    make_mock_async_stream,
    make_mock_conversation,
    make_platform_message,
)


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
        config = LettaAdapterConfig()
        adapter = LettaAdapter(config=config, emit=())
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
