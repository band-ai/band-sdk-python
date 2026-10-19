"""Codex adapter: custom tools are offered to Codex and dispatched before platform tools."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.runtime.custom_tools import CustomToolDef
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)


class TestCustomTools:
    @pytest.mark.asyncio
    async def test_custom_tools_schemas_merged_into_dynamic_tools(self) -> None:
        """Custom tool schemas appear in _build_dynamic_tools output."""

        class WeatherInput(BaseModel):
            """Get current weather for a location."""

            city: str

        def get_weather(inp: WeatherInput) -> str:
            return f"Sunny in {inp.city}"

        custom_tools: list[CustomToolDef] = [(WeatherInput, get_weather)]
        adapter = CodexAdapter(
            config=CodexAdapterConfig(),
            additional_tools=custom_tools,
        )

        tools = ToolSchemaFakeTools()
        dynamic_tools = adapter._build_dynamic_tools(tools)

        names = [t["name"] for t in dynamic_tools]
        assert "weather" in names

        weather_tool = next(t for t in dynamic_tools if t["name"] == "weather")
        assert weather_tool["description"] == "Get current weather for a location."
        assert "inputSchema" in weather_tool
        assert "city" in weather_tool["inputSchema"].get("properties", {})

    @pytest.mark.asyncio
    async def test_custom_tool_dispatched_before_platform_tools(self) -> None:
        """Custom tool is invoked via execute_custom_tool, not platform tools."""

        class CalculatorInput(BaseModel):
            """Simple calculator."""

            expression: str

        call_log: list[str] = []

        async def calculate(inp: CalculatorInput) -> str:
            call_log.append(inp.expression)
            return "42"

        custom_tools: list[CustomToolDef] = [(CalculatorInput, calculate)]
        events = [
            event_request(
                99,
                "item/tool/call",
                {
                    "tool": "calculator",
                    "arguments": {"expression": "6*7"},
                    "callId": "call-99",
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), additional_tools=custom_tools
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        # Custom tool was called
        assert call_log == ["6*7"]
        # Platform execute_tool_call was NOT called for the custom tool
        assert not any(tc["tool_name"] == "calculator" for tc in tools.tool_calls)
        # Response was sent back to Codex
        assert fake_client.responses
        _, payload = fake_client.responses[0]
        assert payload["success"] is True
        assert payload["contentItems"][0]["text"] == "42"
