"""Portable tool schemas preserve descriptions and input fields."""

from __future__ import annotations

from band.runtime.custom_tools import (
    custom_tool_to_anthropic_schema,
    custom_tool_to_openai_schema,
)
from tests.runtime.customtools.kit import (
    CalculatorInput,
    NoDocstringInput,
    WeatherInput,
)


class TestCustomToolSchemas:
    """Test schema generation for custom tools."""

    def test_openai_schema_structure(self) -> None:
        """OpenAI schema should have type=function with nested function object."""
        schema = custom_tool_to_openai_schema(WeatherInput)

        assert schema["type"] == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "weather"
        assert schema["function"]["description"] == "Get current weather for a city."
        assert "parameters" in schema["function"]

    def test_openai_schema_parameters(self) -> None:
        """OpenAI schema parameters should contain field definitions."""
        schema = custom_tool_to_openai_schema(CalculatorInput)

        params = schema["function"]["parameters"]
        assert "properties" in params
        assert "operation" in params["properties"]
        assert "left" in params["properties"]
        assert "right" in params["properties"]

    def test_openai_schema_no_title(self) -> None:
        """OpenAI schema should not include 'title' field."""
        schema = custom_tool_to_openai_schema(WeatherInput)

        assert "title" not in schema["function"]["parameters"]

    def test_openai_schema_empty_docstring(self) -> None:
        """OpenAI schema should handle missing docstring."""
        schema = custom_tool_to_openai_schema(NoDocstringInput)

        assert schema["function"]["description"] == ""

    def test_anthropic_schema_structure(self) -> None:
        """Anthropic schema should have name, description, input_schema."""
        schema = custom_tool_to_anthropic_schema(WeatherInput)

        assert schema["name"] == "weather"
        assert schema["description"] == "Get current weather for a city."
        assert "input_schema" in schema

    def test_anthropic_schema_input_schema(self) -> None:
        """Anthropic schema input_schema should contain field definitions."""
        schema = custom_tool_to_anthropic_schema(CalculatorInput)

        input_schema = schema["input_schema"]
        assert "properties" in input_schema
        assert "operation" in input_schema["properties"]
        assert "left" in input_schema["properties"]
        assert "right" in input_schema["properties"]

    def test_anthropic_schema_no_title(self) -> None:
        """Anthropic schema should not include 'title' field."""
        schema = custom_tool_to_anthropic_schema(WeatherInput)

        assert "title" not in schema["input_schema"]

    def test_anthropic_schema_empty_docstring(self) -> None:
        """Anthropic schema should handle missing docstring."""
        schema = custom_tool_to_anthropic_schema(NoDocstringInput)

        assert schema["description"] == ""
