from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from letta_client.types.agents.text_content import TextContent
from letta_client.types.agents.tool_call import ToolCall
from letta_client.types.agents.tool_return import ToolReturn

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    RoomContext,
)
from band.converters.letta import LettaSessionState
from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.letta.kit import reported_tool_names
from tests.adapters.lettakit import (
    make_assistant_message,
    make_grouped_call_message,
    make_grouped_return_message,
    make_letta_response,
    make_platform_message,
    make_tool_call_message,
    make_tool_return_message,
    ready_letta_adapter,
)


class TestExecutionReporting:
    @pytest.mark.asyncio
    async def test_reports_non_silent_tool_calls(self) -> None:
        config = LettaAdapterConfig()
        adapter = LettaAdapter(config=config, emit=Emit.TOOL_CALLS)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

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
    async def test_reports_every_parallel_call_and_its_result(self) -> None:
        adapter, mock_client = ready_letta_adapter(emit=Emit.TOOL_CALLS)
        calls = [
            ToolCall(name=BandTool.GET_PARTICIPANTS, arguments="{}", tool_call_id="a"),
            ToolCall(name=BandTool.LOOKUP_PEERS, arguments="{}", tool_call_id="b"),
        ]
        multimodal_output = [TextContent(type="text", text="no peers")]
        returns = [
            ToolReturn(
                type="tool",
                status="success",
                tool_call_id="b",
                tool_return=multimodal_output,
            ),
            ToolReturn(
                type="tool", status="success", tool_call_id="a", tool_return="[]"
            ),
        ]
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_grouped_call_message(calls),
            make_grouped_return_message(returns, name=calls[0].name),
            make_assistant_message("Done"),
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

        assert reported_tool_names(tools, "tool_call") == [
            BandTool.GET_PARTICIPANTS,
            BandTool.LOOKUP_PEERS,
        ]
        assert reported_tool_names(tools, "tool_result") == [
            BandTool.LOOKUP_PEERS,
            BandTool.GET_PARTICIPANTS,
        ]

    @pytest.mark.asyncio
    async def test_silent_tools_not_reported(self) -> None:
        config = LettaAdapterConfig()
        adapter = LettaAdapter(config=config, emit=Emit.TOOL_CALLS)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

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

    @pytest.mark.asyncio
    async def test_send_room_file_reports_content_placeholder_not_raw_bytes(
        self,
    ) -> None:
        """ToolCall.arguments is a raw JSON string (letta_client's own wire
        shape); the tool_call event must still redact band_send_room_file's
        content field, not embed the real file bytes verbatim."""
        config = LettaAdapterConfig()
        adapter = LettaAdapter(config=config, emit=Emit.TOOL_CALLS)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message(
                "band_send_room_file",
                '{"content": "SECRET FILE BYTES", "filename": "f.txt"}',
            ),
            make_tool_return_message("band_send_room_file", '{"status": "ok"}'),
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
        assert len(tool_call_events) == 1
        reported_args = json.loads(tool_call_events[0]["content"])["args"]
        assert "SECRET FILE BYTES" not in json.dumps(reported_args)
        assert "byte file content" in reported_args["content"]
        assert reported_args["filename"] == "f.txt"
