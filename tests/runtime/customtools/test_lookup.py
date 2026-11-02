"""Portable custom tools are found by their derived names."""

from __future__ import annotations

from band.runtime.custom_tools import (
    CustomToolDef,
    find_custom_tool,
)
from tests.runtime.customtools.kit import (
    CalculatorInput,
    WeatherInput,
    async_weather,
    failing_tool,
    sync_calculator,
)


class TestFindCustomTool:
    """Test custom tool lookup by name."""

    def test_finds_tool_by_name(self) -> None:
        """Should return matching (model, func) tuple."""
        tools: list[CustomToolDef] = [
            (WeatherInput, async_weather),
            (CalculatorInput, sync_calculator),
        ]

        result = find_custom_tool(tools, "calculator")

        assert result is not None
        assert result[0] is CalculatorInput
        assert result[1] is sync_calculator

    def test_returns_none_for_unknown_tool(self) -> None:
        """Should return None if tool not found."""
        tools: list[CustomToolDef] = [
            (WeatherInput, async_weather),
        ]

        result = find_custom_tool(tools, "unknown")

        assert result is None

    def test_handles_empty_list(self) -> None:
        """Should return None for empty tool list."""
        result = find_custom_tool([], "weather")

        assert result is None

    def test_finds_first_match(self) -> None:
        """Should return first matching tool if duplicates exist."""
        first_func = async_weather
        second_func = failing_tool  # Different func, same model
        tools: list[CustomToolDef] = [
            (WeatherInput, first_func),
            (WeatherInput, second_func),
        ]

        result = find_custom_tool(tools, "weather")

        assert result is not None
        assert result[1] is first_func
