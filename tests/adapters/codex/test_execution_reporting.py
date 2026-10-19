"""Codex adapter: tool calls and results are reported as Band execution events."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.core.types import Emit
from band.runtime.custom_tools import CustomToolDef
from band.testing import (
    events_of_type,
)
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestExecutionReporting:
    @pytest.mark.asyncio
    async def test_execution_reporting_emits_tool_call_and_result_events(self) -> None:
        """With emit=Emit.TOOL_CALLS, tool_call and tool_result events are emitted."""
        events = [
            event_request(
                50,
                "item/tool/call",
                {
                    "tool": "band_lookup_peers",
                    "arguments": {"page": 1},
                    "callId": "call-50",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "band_lookup_peers"
        assert call_data["tool_call_id"] == "call-50"

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "band_lookup_peers"
        assert result_data["tool_call_id"] == "call-50"

    @pytest.mark.asyncio
    async def test_send_room_file_tool_call_event_redacts_content(self) -> None:
        """band_send_room_file's raw content must never reach a tool_call
        event -- report has no idea content can carry real file bytes."""
        raw_content = "raw file bytes that must never reach a tool_call event"
        events = [
            tool_call_request(
                50, "band_send_room_file", {"content": raw_content, "filename": "f.txt"}
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        call_data = json.loads(events_of_type(tools, "tool_call")[0]["content"])
        assert (
            call_data["args"]["content"]
            == f"<{len(raw_content.encode('utf-8'))} byte file content>"
        )
        assert raw_content not in json.dumps(call_data)

    @pytest.mark.asyncio
    async def test_execution_reporting_silenced_with_explicit_empty_emit(self) -> None:
        """emit=() silences tool_call/tool_result events (emit otherwise defaults on)."""
        events = [
            event_request(
                50,
                "item/tool/call",
                {
                    "tool": "band_lookup_peers",
                    "arguments": {"page": 1},
                    "callId": "call-50",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig(), emit=())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result"}
        ]
        assert tool_events == []

    @pytest.mark.asyncio
    async def test_execution_reporting_on_tool_error(self) -> None:
        """Execution reporting emits tool_result with error text on failure."""

        class FailInput(BaseModel):
            """A tool that always fails."""

            x: int

        async def fail_func(inp: FailInput) -> str:
            raise RuntimeError("boom")

        custom_tools: list[CustomToolDef] = [(FailInput, fail_func)]
        events = [
            event_request(
                60,
                "item/tool/call",
                {
                    "tool": "fail",
                    "arguments": {"x": 1},
                    "callId": "call-60",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(),
            additional_tools=custom_tools,
            emit=Emit.TOOL_CALLS,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_result_events) == 1
        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "fail"
        assert "boom" in result_data["output"]
        assert result_data["tool_call_id"] == "call-60"

        # Codex response should indicate failure
        _, payload = fake_client.responses[0]
        assert payload["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name",
        ["band_send_event", "band_send_message"],
    )
    async def test_execution_reporting_emitted_for_platform_output_tools(
        self, tool_name: str
    ) -> None:
        """Band messaging tools are reported like any other tool — no suppression."""
        events = [
            event_request(
                70,
                "item/tool/call",
                {
                    "tool": tool_name,
                    "arguments": {"content": "test", "message_type": "thought"},
                    "callId": "call-70",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        # The tool call itself should still execute
        assert len(tools.tool_calls) == 1
        assert tools.tool_calls[0]["tool_name"] == tool_name

        # And it's reported like any other tool call
        reporting_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result"}
        ]
        assert [e["message_type"] for e in reporting_events] == [
            "tool_call",
            "tool_result",
        ]
