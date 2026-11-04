"""CustomTools behavior of the Google ADK adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from band.testing import (
    FakeAgentTools,
)
from tests.adapters.googleadk.bridge import GoogleADKAdapter, _BandToolBridge
from tests.framework_conformance.turnprobes import (
    CUSTOM_TOOL_DECLARATIONS,
)


class TestCustomTools:
    """Tests for custom tool support."""

    def test_accepts_additional_tools_parameter(self):
        """Adapter should accept list of (Model, func) tuples."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        adapter = GoogleADKAdapter(additional_tools=[(EchoInput, echo)])
        assert len(adapter._custom_tools) == 1

    @pytest.mark.asyncio
    async def test_custom_tool_execution_via_bridge(self):
        """Custom tool should be executed via bridge."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        bridge = _BandToolBridge(
            tool_name="echo",
            tool_description="Echo back the message",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(EchoInput, echo)],
        )

        result = await bridge.run_async(
            args={"message": "Hello"},
            tool_context=MagicMock(),
        )

        # Should NOT have called platform tool
        mock_tools.execute_tool_call.assert_not_called()
        assert "Echo: Hello" in result

    @pytest.mark.asyncio
    async def test_sync_custom_tool_execution(self):
        """Sync custom tool should be executed via bridge."""

        class CalcInput(BaseModel):
            """Calculate sum."""

            a: int = Field(description="First number")
            b: int = Field(description="Second number")

        def calculate(args: CalcInput) -> str:
            return f"Result: {args.a + args.b}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        # Tool name derived from model: CalcInput -> "calc"
        bridge = _BandToolBridge(
            tool_name="calc",
            tool_description="Calculate sum",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(CalcInput, calculate)],
        )

        result = await bridge.run_async(
            args={"a": 3, "b": 4},
            tool_context=MagicMock(),
        )

        mock_tools.execute_tool_call.assert_not_called()
        assert "Result: 7" in result

    @pytest.mark.asyncio
    async def test_custom_tool_validation_error(self):
        """Should return formatted validation error for invalid arguments."""

        class StrictInput(BaseModel):
            """Requires a name field."""

            name: str = Field(description="Required name")

        async def strict_tool(args: StrictInput) -> str:
            return f"Hello, {args.name}"

        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        bridge = _BandToolBridge(
            tool_name="strict",
            tool_description="Requires name",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[(StrictInput, strict_tool)],
        )

        # Pass empty args - missing required 'name' field
        result = await bridge.run_async(
            args={},
            tool_context=MagicMock(),
        )

        assert result == {
            "ok": False,
            "error": "Invalid arguments for strict: name: Field required",
        }

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_execution(self):
        """Multiple custom tools should be independently executable."""

        class EchoInput(BaseModel):
            """Echo back the message."""

            message: str = Field(description="Message to echo")

        class CalcInput(BaseModel):
            """Calculate sum."""

            a: int = Field(description="First number")
            b: int = Field(description="Second number")

        async def echo(args: EchoInput) -> str:
            return f"Echo: {args.message}"

        def calculate(args: CalcInput) -> str:
            return f"Sum: {args.a + args.b}"

        custom_tools = [(EchoInput, echo), (CalcInput, calculate)]
        mock_tools = MagicMock()
        mock_tools.execute_tool_call = AsyncMock()

        echo_bridge = _BandToolBridge(
            tool_name="echo",
            tool_description="Echo",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=custom_tools,
        )
        # Tool name derived from model: CalcInput -> "calc"
        calc_bridge = _BandToolBridge(
            tool_name="calc",
            tool_description="Calc",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=custom_tools,
        )

        echo_result = await echo_bridge.run_async(
            args={"message": "Hi"}, tool_context=MagicMock()
        )
        calc_result = await calc_bridge.run_async(
            args={"a": 2, "b": 3}, tool_context=MagicMock()
        )

        assert "Echo: Hi" in echo_result
        assert "Sum: 5" in calc_result
        mock_tools.execute_tool_call.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
    async def test_custom_tool_records_its_effect_on_the_room_turn(
        self, declare: Callable[..., Any], complete: bool
    ):
        class FileInput(BaseModel):
            """File the report."""

            note: str

        @declare
        async def file_report(args: FileInput) -> str:
            return "filed"

        tools = FakeAgentTools(room_id="room-123")
        adapter = GoogleADKAdapter(additional_tools=[(FileInput, file_report)])
        (bridge,) = [t for t in adapter._build_adk_tools(tools) if t.name == "file"]

        await bridge.run_async(args={"note": "go"}, tool_context=MagicMock())

        assert tools.turn.complete is complete
