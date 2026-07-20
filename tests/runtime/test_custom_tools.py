"""Tests for custom tools utilities."""

import pytest
from pydantic import BaseModel, Field

from band.runtime.custom_tools import (
    CustomToolDef,
    custom_tool_to_anthropic_schema,
    custom_tool_to_openai_schema,
    custom_tools_to_schemas,
    execute_custom_tool,
    find_custom_tool,
    get_custom_tool_name,
)


# Test fixtures - sample tool definitions
class WeatherInput(BaseModel):
    """Get current weather for a city."""

    city: str = Field(description="City name")


class CalculatorInput(BaseModel):
    """Perform math calculations."""

    operation: str = Field(description="add, subtract, multiply, divide")
    left: float
    right: float


class SearchWebInput(BaseModel):
    """Search the web for information."""

    query: str = Field(description="Search query")
    max_results: int = Field(default=10, description="Maximum results to return")


class NoDocstringInput(BaseModel):
    value: str


async def async_weather(args: WeatherInput) -> str:
    """Async weather implementation."""
    return f"Weather in {args.city}: Sunny, 72F"


def sync_calculator(args: CalculatorInput) -> str:
    """Sync calculator implementation."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }
    result = ops[args.operation](args.left, args.right)
    return str(result)


async def failing_tool(args: WeatherInput) -> str:
    """Tool that raises an exception."""
    raise ValueError("API unavailable")


class TestGetCustomToolName:
    """Test tool name derivation from model class."""

    def test_removes_input_suffix(self):
        """Should remove 'Input' suffix and lowercase."""
        assert get_custom_tool_name(WeatherInput) == "weather"
        assert get_custom_tool_name(CalculatorInput) == "calculator"

    def test_handles_multi_word_names(self):
        """Should lowercase multi-word names."""
        assert get_custom_tool_name(SearchWebInput) == "searchweb"

    def test_handles_no_input_suffix(self):
        """Should just lowercase if no 'Input' suffix."""

        class MyTool(BaseModel):
            value: str

        assert get_custom_tool_name(MyTool) == "mytool"


class TestCustomToolSchemas:
    """Test schema generation for custom tools."""

    def test_openai_schema_structure(self):
        """OpenAI schema should have type=function with nested function object."""
        schema = custom_tool_to_openai_schema(WeatherInput)

        assert schema["type"] == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "weather"
        assert schema["function"]["description"] == "Get current weather for a city."
        assert "parameters" in schema["function"]

    def test_openai_schema_parameters(self):
        """OpenAI schema parameters should contain field definitions."""
        schema = custom_tool_to_openai_schema(CalculatorInput)

        params = schema["function"]["parameters"]
        assert "properties" in params
        assert "operation" in params["properties"]
        assert "left" in params["properties"]
        assert "right" in params["properties"]

    def test_openai_schema_no_title(self):
        """OpenAI schema should not include 'title' field."""
        schema = custom_tool_to_openai_schema(WeatherInput)

        assert "title" not in schema["function"]["parameters"]

    def test_openai_schema_empty_docstring(self):
        """OpenAI schema should handle missing docstring."""
        schema = custom_tool_to_openai_schema(NoDocstringInput)

        assert schema["function"]["description"] == ""

    def test_anthropic_schema_structure(self):
        """Anthropic schema should have name, description, input_schema."""
        schema = custom_tool_to_anthropic_schema(WeatherInput)

        assert schema["name"] == "weather"
        assert schema["description"] == "Get current weather for a city."
        assert "input_schema" in schema

    def test_anthropic_schema_input_schema(self):
        """Anthropic schema input_schema should contain field definitions."""
        schema = custom_tool_to_anthropic_schema(CalculatorInput)

        input_schema = schema["input_schema"]
        assert "properties" in input_schema
        assert "operation" in input_schema["properties"]
        assert "left" in input_schema["properties"]
        assert "right" in input_schema["properties"]

    def test_anthropic_schema_no_title(self):
        """Anthropic schema should not include 'title' field."""
        schema = custom_tool_to_anthropic_schema(WeatherInput)

        assert "title" not in schema["input_schema"]

    def test_anthropic_schema_empty_docstring(self):
        """Anthropic schema should handle missing docstring."""
        schema = custom_tool_to_anthropic_schema(NoDocstringInput)

        assert schema["description"] == ""


class TestCustomToolsToSchemas:
    """Test batch schema conversion."""

    def test_converts_multiple_tools_openai(self):
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

    def test_converts_multiple_tools_anthropic(self):
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

    def test_handles_empty_list(self):
        """Should return empty list for empty input."""
        schemas = custom_tools_to_schemas([], "openai")

        assert schemas == []


class TestFindCustomTool:
    """Test custom tool lookup by name."""

    def test_finds_tool_by_name(self):
        """Should return matching (model, func) tuple."""
        tools: list[CustomToolDef] = [
            (WeatherInput, async_weather),
            (CalculatorInput, sync_calculator),
        ]

        result = find_custom_tool(tools, "calculator")

        assert result is not None
        assert result[0] is CalculatorInput
        assert result[1] is sync_calculator

    def test_returns_none_for_unknown_tool(self):
        """Should return None if tool not found."""
        tools: list[CustomToolDef] = [
            (WeatherInput, async_weather),
        ]

        result = find_custom_tool(tools, "unknown")

        assert result is None

    def test_handles_empty_list(self):
        """Should return None for empty tool list."""
        result = find_custom_tool([], "weather")

        assert result is None

    def test_finds_first_match(self):
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


class TestExecuteCustomTool:
    """Test custom tool execution with validation."""

    @pytest.mark.asyncio
    async def test_executes_async_function(self):
        """Should await async tool functions."""
        tool: CustomToolDef = (WeatherInput, async_weather)

        result = await execute_custom_tool(tool, {"city": "NYC"})

        assert result == "Weather in NYC: Sunny, 72F"

    @pytest.mark.asyncio
    async def test_executes_sync_function(self):
        """Should handle sync tool functions."""
        tool: CustomToolDef = (CalculatorInput, sync_calculator)

        result = await execute_custom_tool(
            tool, {"operation": "add", "left": 5.0, "right": 3.0}
        )

        assert result == "8.0"

    @pytest.mark.asyncio
    async def test_awaits_callable_object_with_async_call(self):
        """A callable object whose __call__ is async must be awaited, not returned
        raw — asyncio.iscoroutinefunction is False for such instances, so a bare
        check would leak an unawaited coroutine."""

        class AsyncTool:
            async def __call__(self, args: WeatherInput) -> str:
                return f"Weather in {args.city}: from async-callable"

        tool: CustomToolDef = (WeatherInput, AsyncTool())

        result = await execute_custom_tool(tool, {"city": "NYC"})

        assert result == "Weather in NYC: from async-callable"

    @pytest.mark.asyncio
    async def test_runs_unhashable_callable_handler(self):
        """A callable object that defines __eq__ (and is therefore unhashable) is a
        valid handler and must run — not raise on a cache lookup."""

        class UnhashableTool:
            def __eq__(self, other: object) -> bool:
                return isinstance(other, UnhashableTool)

            async def __call__(self, args: WeatherInput) -> str:
                return f"Weather in {args.city}: unhashable-ok"

        tool: CustomToolDef = (WeatherInput, UnhashableTool())

        result = await execute_custom_tool(tool, {"city": "LA"})

        assert result == "Weather in LA: unhashable-ok"

    @pytest.mark.asyncio
    async def test_validates_input_with_pydantic(self):
        """Should raise ValueError with formatted message for invalid args."""
        tool: CustomToolDef = (CalculatorInput, sync_calculator)

        with pytest.raises(ValueError, match="Invalid arguments for calculator"):
            await execute_custom_tool(tool, {"operation": "add"})  # Missing left, right

    @pytest.mark.asyncio
    async def test_validation_error_has_details(self):
        """ValueError should contain field information in LLM-friendly format."""
        tool: CustomToolDef = (WeatherInput, async_weather)

        with pytest.raises(ValueError) as exc_info:
            await execute_custom_tool(tool, {})  # Missing required 'city'

        # Should mention the missing field in formatted message
        error_msg = str(exc_info.value)
        assert "city" in error_msg
        assert "Invalid arguments for weather" in error_msg

    @pytest.mark.asyncio
    async def test_execution_error_propagates(self):
        """Tool exceptions should bubble up for adapter to catch."""
        tool: CustomToolDef = (WeatherInput, failing_tool)

        with pytest.raises(ValueError, match="API unavailable"):
            await execute_custom_tool(tool, {"city": "NYC"})

    @pytest.mark.asyncio
    async def test_passes_validated_model_to_function(self):
        """Function should receive Pydantic model, not raw dict."""
        received_args = []

        async def capture_args(args: CalculatorInput) -> str:
            received_args.append(args)
            return "captured"

        tool: CustomToolDef = (CalculatorInput, capture_args)

        await execute_custom_tool(tool, {"operation": "add", "left": 1, "right": 2})

        assert len(received_args) == 1
        assert isinstance(received_args[0], CalculatorInput)
        assert received_args[0].operation == "add"
        assert received_args[0].left == 1.0
        assert received_args[0].right == 2.0

    @pytest.mark.asyncio
    async def test_coerces_types(self):
        """Should coerce compatible types (e.g., int to float)."""
        tool: CustomToolDef = (CalculatorInput, sync_calculator)

        # Pass ints instead of floats
        result = await execute_custom_tool(
            tool, {"operation": "multiply", "left": 4, "right": 5}
        )

        assert result == "20.0"

    @pytest.mark.asyncio
    async def test_handles_optional_fields(self):
        """Should use default values for optional fields."""
        received_args = []

        async def capture_search(args: SearchWebInput) -> str:
            received_args.append(args)
            return "searched"

        tool: CustomToolDef = (SearchWebInput, capture_search)

        await execute_custom_tool(tool, {"query": "test"})  # No max_results

        assert received_args[0].max_results == 10  # Default value
