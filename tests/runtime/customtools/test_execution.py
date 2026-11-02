"""Portable tool execution validates inputs and awaits handlers."""

from __future__ import annotations

import pytest

from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
)
from tests.runtime.customtools.kit import (
    CalculatorInput,
    SearchWebInput,
    WeatherInput,
    async_weather,
    failing_tool,
    sync_calculator,
)


class TestExecuteCustomTool:
    """Test custom tool execution with validation."""

    @pytest.mark.asyncio
    async def test_executes_async_function(self) -> None:
        """Should await async tool functions."""
        tool: CustomToolDef = (WeatherInput, async_weather)

        result = await execute_custom_tool(tool, {"city": "NYC"}, tools=None)

        assert result == "Weather in NYC: Sunny, 72F"

    @pytest.mark.asyncio
    async def test_executes_sync_function(self) -> None:
        """Should handle sync tool functions."""
        tool: CustomToolDef = (CalculatorInput, sync_calculator)

        result = await execute_custom_tool(
            tool, {"operation": "add", "left": 5.0, "right": 3.0}, tools=None
        )

        assert result == "8.0"

    @pytest.mark.asyncio
    async def test_awaits_callable_object_with_async_call(self) -> None:
        """A callable object whose __call__ is async must be awaited, not returned
        raw — asyncio.iscoroutinefunction is False for such instances, so a bare
        check would leak an unawaited coroutine."""

        class AsyncTool:
            async def __call__(self, args: WeatherInput) -> str:
                return f"Weather in {args.city}: from async-callable"

        tool: CustomToolDef = (WeatherInput, AsyncTool())

        result = await execute_custom_tool(tool, {"city": "NYC"}, tools=None)

        assert result == "Weather in NYC: from async-callable"

    @pytest.mark.asyncio
    async def test_runs_unhashable_callable_handler(self) -> None:
        """A callable object that defines __eq__ (and is therefore unhashable) is a
        valid handler and must run — not raise on a cache lookup."""

        class UnhashableTool:
            def __eq__(self, other: object) -> bool:
                return isinstance(other, UnhashableTool)

            async def __call__(self, args: WeatherInput) -> str:
                return f"Weather in {args.city}: unhashable-ok"

        tool: CustomToolDef = (WeatherInput, UnhashableTool())

        result = await execute_custom_tool(tool, {"city": "LA"}, tools=None)

        assert result == "Weather in LA: unhashable-ok"

    @pytest.mark.asyncio
    async def test_validates_input_with_pydantic(self) -> None:
        """Should raise ValueError with formatted message for invalid args."""
        tool: CustomToolDef = (CalculatorInput, sync_calculator)

        with pytest.raises(ValueError, match="Invalid arguments for calculator"):
            await execute_custom_tool(
                tool, {"operation": "add"}, tools=None
            )  # Missing left, right

    @pytest.mark.asyncio
    async def test_validation_error_has_details(self) -> None:
        """ValueError should contain field information in LLM-friendly format."""
        tool: CustomToolDef = (WeatherInput, async_weather)

        with pytest.raises(ValueError) as exc_info:
            await execute_custom_tool(tool, {}, tools=None)  # Missing required 'city'

        # Should mention the missing field in formatted message
        error_msg = str(exc_info.value)
        assert "city" in error_msg
        assert "Invalid arguments for weather" in error_msg

    @pytest.mark.asyncio
    async def test_execution_error_propagates(self) -> None:
        """Tool exceptions should bubble up for adapter to catch."""
        tool: CustomToolDef = (WeatherInput, failing_tool)

        with pytest.raises(ValueError, match="API unavailable"):
            await execute_custom_tool(tool, {"city": "NYC"}, tools=None)

    @pytest.mark.asyncio
    async def test_passes_validated_model_to_function(self) -> None:
        """Function should receive Pydantic model, not raw dict."""
        received_args = []

        async def capture_args(args: CalculatorInput) -> str:
            received_args.append(args)
            return "captured"

        tool: CustomToolDef = (CalculatorInput, capture_args)

        await execute_custom_tool(
            tool, {"operation": "add", "left": 1, "right": 2}, tools=None
        )

        assert len(received_args) == 1
        assert isinstance(received_args[0], CalculatorInput)
        assert received_args[0].operation == "add"
        assert received_args[0].left == 1.0
        assert received_args[0].right == 2.0

    @pytest.mark.asyncio
    async def test_coerces_types(self) -> None:
        """Should coerce compatible types (e.g., int to float)."""
        tool: CustomToolDef = (CalculatorInput, sync_calculator)

        # Pass ints instead of floats
        result = await execute_custom_tool(
            tool, {"operation": "multiply", "left": 4, "right": 5}, tools=None
        )

        assert result == "20.0"

    @pytest.mark.asyncio
    async def test_handles_optional_fields(self) -> None:
        """Should use default values for optional fields."""
        received_args = []

        async def capture_search(args: SearchWebInput) -> str:
            received_args.append(args)
            return "searched"

        tool: CustomToolDef = (SearchWebInput, capture_search)

        await execute_custom_tool(tool, {"query": "test"}, tools=None)  # No max_results

        assert received_args[0].max_results == 10  # Default value
