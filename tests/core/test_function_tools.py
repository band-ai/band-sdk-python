"""Tests for Phase 3A FunctionTool artifacts."""

from __future__ import annotations

import warnings
from enum import Enum

import pytest
from pydantic import BaseModel, Field

from band.core.exceptions import DuplicateToolError
from band.core.tools import (
    FunctionTool,
    ToolContext,
    ToolSpec,
    normalize_additional_tools,
    tool,
    tool_spec_to_anthropic_schema,
    tool_spec_to_openai_schema,
)
from band.runtime.custom_tools import CustomToolDef, custom_tool_to_anthropic_schema


class _Status(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@tool
async def weather(city: str) -> str:
    """Get weather for a city."""
    return f"Sunny in {city}"


@tool(name="calc", terminal=True)
def calc(x: int) -> int:
    """Add one."""
    return x + 1


@tool(lenient=True)
def lenient_tool(good: str, bad: object) -> str:
    return good


class TestToolDecorator:
    def test_builds_tool_spec_with_properties(self) -> None:
        function_tool = weather.__band_function_tool__  # type: ignore[attr-defined]
        spec = function_tool.spec()

        assert isinstance(spec, ToolSpec)
        assert spec.name == "weather"
        assert spec.description == "Get weather for a city."
        assert "city" in spec.parameters["properties"]
        assert "city" in spec.parameters.get("required", [])

    def test_strict_rejects_bad_annotation(self) -> None:
        with pytest.raises(ValueError, match="unsupported_param"):

            @tool
            def bad_tool(supported: str, unsupported_param: object) -> str:
                return supported

    def test_lenient_skips_with_warning(self) -> None:
        function_tool = lenient_tool.__band_function_tool__  # type: ignore[attr-defined]
        spec = function_tool.spec()

        assert "good" in spec.parameters["properties"]
        assert "bad" not in spec.parameters["properties"]

    @pytest.mark.asyncio
    async def test_execute_async_tool(self) -> None:
        function_tool = weather.__band_function_tool__  # type: ignore[attr-defined]
        result = await function_tool.execute({"city": "Paris"})
        assert result == "Sunny in Paris"


class TestTerminalTool:
    def test_terminal_sets_band_terminal_on_wrapper(self) -> None:
        function_tool = calc.__band_function_tool__  # type: ignore[attr-defined]
        _, handler = function_tool.as_custom_tool_def()

        assert getattr(handler, "band_terminal", False) is True


class TestToolContext:
    @tool
    def with_ctx(ctx: ToolContext, query: str) -> str:
        """Search with context."""
        return f"{query}:{ctx.tools is None}"

    def test_context_excluded_from_schema(self) -> None:
        function_tool = self.with_ctx.__band_function_tool__  # type: ignore[attr-defined]
        spec = function_tool.spec()

        assert function_tool.needs_context is True
        assert "ctx" not in spec.parameters["properties"]
        assert "query" in spec.parameters["properties"]

    @pytest.mark.asyncio
    async def test_execute_injects_context(self) -> None:
        function_tool = self.with_ctx.__band_function_tool__  # type: ignore[attr-defined]
        ctx = ToolContext(tools="band-tools")

        result = await function_tool.execute({"query": "hello"}, context=ctx)

        assert result == "hello:False"


class TestFromCustomToolDef:
    class WeatherInput(BaseModel):
        """Get current weather."""

        city: str = Field(description="City name")

    @staticmethod
    async def _handler(args: WeatherInput) -> str:
        return f"Weather in {args.city}"

    def test_roundtrip(self) -> None:
        original: CustomToolDef = (self.WeatherInput, self._handler)
        function_tool = FunctionTool.from_custom_tool_def(original, terminal=True)

        assert function_tool.name == "weather"
        assert function_tool.description == "Get current weather."
        assert function_tool.terminal is True

        model, handler = function_tool.as_custom_tool_def()
        assert model is self.WeatherInput
        assert getattr(handler, "band_terminal", False) is True


class TestNormalizeAdditionalTools:
    def test_none_returns_empty(self) -> None:
        assert normalize_additional_tools(None) == []

    def test_function_tool_passthrough(self) -> None:
        function_tool = weather.__band_function_tool__  # type: ignore[attr-defined]
        assert normalize_additional_tools([function_tool]) == [function_tool]

    def test_tuple_raises_with_migration_guidance(self) -> None:
        class EchoInput(BaseModel):
            value: str

        def handler(args: EchoInput) -> str:
            return args.value

        with pytest.raises(TypeError, match="FunctionTool.from_custom_tool_def"):
            normalize_additional_tools([(EchoInput, handler)])  # type: ignore[list-item]

    def test_duplicate_raises(self) -> None:
        function_tool = weather.__band_function_tool__  # type: ignore[attr-defined]

        with pytest.raises(DuplicateToolError, match="weather"):
            normalize_additional_tools([function_tool, function_tool])

    def test_bare_callable_gets_decorated(self) -> None:
        def echo(message: str) -> str:
            """Echo a message."""
            return message

        normalized = normalize_additional_tools([echo])
        assert normalized[0].name == "echo"
        assert normalized[0].description == "Echo a message."


class TestSchemaTranslators:
    def test_openai_schema_shape(self) -> None:
        function_tool = weather.__band_function_tool__  # type: ignore[attr-defined]
        schema = tool_spec_to_openai_schema(function_tool.spec())

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "weather"
        assert "parameters" in schema["function"]
        assert "title" not in schema["function"]["parameters"]

    def test_anthropic_schema_shape(self) -> None:
        function_tool = weather.__band_function_tool__  # type: ignore[attr-defined]
        schema = tool_spec_to_anthropic_schema(function_tool.spec())

        assert schema["name"] == "weather"
        assert schema["description"] == "Get weather for a city."
        assert "input_schema" in schema
        assert "title" not in schema["input_schema"]

    def test_custom_tools_wrapper_matches_anthropic_shape(self) -> None:
        class WeatherInput(BaseModel):
            """Get current weather for a city."""

            city: str

        via_wrapper = custom_tool_to_anthropic_schema(WeatherInput)
        function_tool = FunctionTool.from_custom_tool_def((WeatherInput, lambda _: ""))
        via_spec = tool_spec_to_anthropic_schema(function_tool.spec())

        assert via_wrapper == via_spec

    def test_literal_and_enum_supported(self) -> None:
        @tool
        def tagged(value: _Status, label: str) -> str:
            return f"{label}:{value.value}"

        function_tool = tagged.__band_function_tool__  # type: ignore[attr-defined]
        spec = function_tool.spec()

        assert "value" in spec.parameters["properties"]
        assert "label" in spec.parameters["properties"]

    def test_lenient_emits_user_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @tool(lenient=True)
            def skip_bad(good: int, bad: object) -> int:
                return good

        assert any(isinstance(w.message, UserWarning) for w in caught)


class TestReviewRegressions:
    """Guards for Phase 3A review defects."""

    def test_simple_adapter_still_exported_from_band_core(self) -> None:
        from band.core import SimpleAdapter

        assert SimpleAdapter is not None

    def test_pep604_optional_str_supported(self) -> None:
        @tool
        def optional_city(city: str | None = None) -> str:
            """Optional city."""
            return city or "nowhere"

        function_tool = optional_city.__band_function_tool__  # type: ignore[attr-defined]
        spec = function_tool.spec()
        assert "city" in spec.parameters["properties"]

    @pytest.mark.parametrize(
        "fn",
        [
            lambda value, /: value,
            lambda *values: values,
            lambda **values: values,
            lambda ctx, /, value="": value,
        ],
    )
    def test_rejects_signature_kinds_it_cannot_invoke(self, fn) -> None:
        with pytest.raises(ValueError, match="Unsupported parameter kind"):
            FunctionTool.from_callable(fn)

    def test_an_unannotated_ctx_stays_a_band_context_param(self) -> None:
        """An unannotated leading ``ctx`` is Band's own context convention.

        Reading it as a framework's injected context instead produced a tool
        advertising no arguments at all, whose wrapper then called the
        function with none of them.
        """

        def weather(ctx, city: str) -> str:  # type: ignore[no-untyped-def]
            """Weather."""
            return city

        normalized = normalize_additional_tools([weather])

        assert normalized[0].native_callable is None
        assert normalized[0].needs_context is True
        assert list(normalized[0].spec().parameters["properties"]) == ["city"]

    def test_bare_callable_gets_a_band_built_schema(self) -> None:
        def echo(message: str) -> str:
            """Echo."""
            return message

        normalized = normalize_additional_tools([echo])
        assert normalized[0].native_callable is None
        assert normalized[0].name == "echo"

    def test_a_framework_that_derives_schemas_keeps_the_callable(self) -> None:
        """An annotation Band's schema builder does not model is not an error
        when the framework reads the signature itself — rejecting it would
        take away every type the framework supports and Band does not."""
        from datetime import datetime

        def remind(when: datetime, text: str) -> str:
            """Remind."""
            return f"{when}:{text}"

        with pytest.raises(ValueError, match="Unsupported annotation"):
            normalize_additional_tools([remind])

        normalized = normalize_additional_tools(
            [remind], framework_derives_schemas=True
        )
        assert normalized[0].native_callable is remind
        assert normalized[0].name == "remind"

    def test_a_decorated_tool_is_never_treated_as_native(self) -> None:
        """``@tool`` is the author saying Band owns this tool's schema."""

        @tool
        def echo(message: str) -> str:
            """Echo."""
            return message

        normalized = normalize_additional_tools([echo], framework_derives_schemas=True)
        assert normalized[0].native_callable is None
        assert "message" in normalized[0].spec().parameters["properties"]

    def test_tool_rename_preserved_in_spec_and_model(self) -> None:
        @tool(name="my_calc")
        def add_one(x: int) -> int:
            """Add one to x."""
            return x + 1

        function_tool = add_one.__band_function_tool__  # type: ignore[attr-defined]
        assert function_tool.name == "my_calc"
        assert function_tool.spec().name == "my_calc"
        assert function_tool.spec().description == "Add one to x."
        assert function_tool.parameters_model.__name__ == "MyCalcInput"
        assert function_tool.parameters_model.__doc__ == "Add one to x."
        assert (
            custom_tool_to_anthropic_schema(function_tool.parameters_model)["name"]
            == "my_calc"
        )
        assert (
            custom_tool_to_anthropic_schema(function_tool.parameters_model)[
                "description"
            ]
            == "Add one to x."
        )

    @pytest.mark.asyncio
    async def test_non_leading_tool_context_injected_by_name(self) -> None:
        @tool
        def search(query: str, ctx: ToolContext) -> str:
            """Search with trailing context."""
            return f"{query}:{ctx.tools}"

        function_tool = search.__band_function_tool__  # type: ignore[attr-defined]
        assert function_tool.needs_context is True
        assert "ctx" not in function_tool.spec().parameters["properties"]
        result = await function_tool.execute(
            {"query": "hi"}, context=ToolContext(tools="band")
        )
        assert result == "hi:band"
