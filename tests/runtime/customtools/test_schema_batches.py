"""Portable tool batches use the requested schema format."""

from __future__ import annotations

from band.runtime.custom_tools import (
    CustomToolDef,
    custom_tools_to_schemas,
)
from tests.runtime.customtools.kit import (
    CalculatorInput,
    WeatherInput,
    async_weather,
    sync_calculator,
)


class TestCustomToolsToSchemas:
    """Test batch schema conversion."""

    def test_converts_multiple_tools_openai(self) -> None:
        """Should convert list of tools to OpenAI format."""
        tools: list[CustomToolDef] = [
            (WeatherInput, async_weather),
            (CalculatorInput, sync_calculator),
        ]

        schemas = custom_tools_to_schemas(tools, "openai")

        assert len(schemas) == 2
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "weather"
        assert schemas[1]["function"]["name"] == "calculator"

    def test_converts_multiple_tools_anthropic(self) -> None:
        """Should convert list of tools to Anthropic format."""
        tools: list[CustomToolDef] = [
            (WeatherInput, async_weather),
            (CalculatorInput, sync_calculator),
        ]

        schemas = custom_tools_to_schemas(tools, "anthropic")

        assert len(schemas) == 2
        assert schemas[0]["name"] == "weather"
        assert "input_schema" in schemas[0]
        assert schemas[1]["name"] == "calculator"

    def test_handles_empty_list(self) -> None:
        """Should return empty list for empty input."""
        schemas = custom_tools_to_schemas([], "openai")

        assert schemas == []
