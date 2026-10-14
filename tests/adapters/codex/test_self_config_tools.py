"""Codex adapter: the model can change its own model and reasoning through tools."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.integrations.codex.types import (
    CodexRequestMethod,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)


class TestSelfConfigTools:
    @pytest.mark.asyncio
    async def test_self_config_tools_registered_when_enabled(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(enable_self_config_tools=True),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="hello", tools=tools)
        # Check that thread/start included setmodel and setreasoning dynamic tools
        thread_params = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        tool_names = [t["name"] for t in thread_params.get("dynamicTools", [])]
        assert "setmodel" in tool_names
        assert "setreasoning" in tool_names

    @pytest.mark.asyncio
    async def test_self_config_tools_not_registered_when_disabled(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(enable_self_config_tools=False),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="hello", tools=tools)
        thread_params = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        tool_names = [t["name"] for t in thread_params.get("dynamicTools", [])]
        assert "setmodel" not in tool_names
        assert "setreasoning" not in tool_names

    @pytest.mark.asyncio
    async def test_setmodel_tool_changes_model(self) -> None:
        events = [
            event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setmodel",
                    "callId": "call-1",
                    "arguments": {"model": "o3"},
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(enable_self_config_tools=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="switch to o3", tools=tools)
        assert adapter._selected_model == "o3"
        # Verify the tool response was sent back
        tool_responses = [
            (rid, result)
            for rid, result in fake_client.responses
            if isinstance(result, dict) and "contentItems" in result
        ]
        assert len(tool_responses) >= 1
        result_text = tool_responses[0][1]["contentItems"][0]["text"]
        assert "o3" in result_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("effort", ["xhigh", "ultra"])
    async def test_setreasoning_tool_changes_effort(self, effort: str) -> None:
        events = [
            event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setreasoning",
                    "callId": "call-2",
                    "arguments": {"effort": effort, "summary": "detailed"},
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(enable_self_config_tools=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="increase reasoning", tools=tools)
        room = adapter._room_clients[ROOM_ID]
        assert room.reasoning_effort == effort
        assert room.reasoning_summary == "detailed"
