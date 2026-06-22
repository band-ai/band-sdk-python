"""
Custom tools utilities for adapters.

Provides helper functions to convert Pydantic models to tool schemas
and execute custom tools with validation.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from functools import lru_cache
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Type alias for custom tool definition: (InputModel, callable)
CustomToolDef = tuple[type[BaseModel], Callable[..., Any]]


def custom_tool_to_mcp_schema(
    input_model: type[BaseModel],
    *,
    include_room_id: bool = False,
) -> dict[str, type]:
    """Convert a Pydantic tool model to the simple MCP SDK schema format."""
    schema = input_model.model_json_schema()
    properties = schema.get("properties", {})
    mcp_schema: dict[str, type] = {"room_id": str} if include_room_id else {}

    for prop_name, prop_def in properties.items():
        prop_type = prop_def.get("type", "string")
        if prop_type == "string":
            mcp_schema[prop_name] = str
        elif prop_type == "number":
            mcp_schema[prop_name] = float
        elif prop_type == "integer":
            mcp_schema[prop_name] = int
        elif prop_type == "boolean":
            mcp_schema[prop_name] = bool
        else:
            mcp_schema[prop_name] = str

    return mcp_schema


def get_custom_tool_name(input_model: type[BaseModel]) -> str:
    """
    Derive tool name from input model class name.

    Convention: Remove "Input" suffix and lowercase.
    Examples:
        WeatherInput -> "weather"
        CalculatorInput -> "calculator"
        SearchWebInput -> "searchweb"
    """
    name = input_model.__name__
    if name.endswith("Input"):
        name = name[:-5]  # Remove "Input" suffix
    return name.lower()


def custom_tool_to_openai_schema(input_model: type[BaseModel]) -> dict[str, Any]:
    """
    Convert Pydantic model to OpenAI function schema.

    Args:
        input_model: Pydantic model class defining tool input

    Returns:
        OpenAI-compatible tool schema with type="function"
    """
    schema = input_model.model_json_schema()
    schema.pop("title", None)  # Remove title, not needed in schema

    return {
        "type": "function",
        "function": {
            "name": get_custom_tool_name(input_model),
            "description": input_model.__doc__ or "",
            "parameters": schema,
        },
    }


def custom_tool_to_anthropic_schema(input_model: type[BaseModel]) -> dict[str, Any]:
    """
    Convert Pydantic model to Anthropic tool schema.

    Args:
        input_model: Pydantic model class defining tool input

    Returns:
        Anthropic-compatible tool schema
    """
    schema = input_model.model_json_schema()
    schema.pop("title", None)  # Remove title, not needed in schema

    return {
        "name": get_custom_tool_name(input_model),
        "description": input_model.__doc__ or "",
        "input_schema": schema,
    }


def custom_tools_to_schemas(
    tools: list[CustomToolDef],
    format: str,
) -> list[dict[str, Any]]:
    """
    Convert list of custom tools to schemas in specified format.

    Args:
        tools: List of (InputModel, callable) tuples
        format: "openai" or "anthropic"

    Returns:
        List of tool schemas in the specified format
    """
    if format == "openai":
        converter = custom_tool_to_openai_schema
    else:
        converter = custom_tool_to_anthropic_schema

    return [converter(model) for model, _ in tools]


def find_custom_tool(
    tools: list[CustomToolDef],
    name: str,
) -> CustomToolDef | None:
    """
    Find custom tool by name.

    Args:
        tools: List of (InputModel, callable) tuples
        name: Tool name to find

    Returns:
        Matching (InputModel, callable) tuple, or None if not found
    """
    for model, func in tools:
        if get_custom_tool_name(model) == name:
            return (model, func)
    return None


@lru_cache(maxsize=256)
def _custom_tool_accepts_input(func: Callable[..., Any]) -> bool:
    try:
        return len(inspect.signature(func).parameters) > 0
    except (TypeError, ValueError):
        return True


async def execute_custom_tool(
    tool: CustomToolDef,
    arguments: dict[str, Any],
) -> Any:
    """
    Execute custom tool with Pydantic validation.

    Args:
        tool: (InputModel, callable) tuple
        arguments: Raw arguments dict from LLM

    Returns:
        Tool execution result

    Raises:
        ValueError: If arguments don't match InputModel schema (formatted for LLM)
        Exception: Any exception from tool function (for adapter to catch)
    """
    model, func = tool

    # Validate arguments, format errors for LLM readability
    try:
        validated = model.model_validate(arguments)
    except ValidationError as e:
        errors = [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid arguments for {tool_name}: {', '.join(errors)}"
        ) from e

    accepts_input = _custom_tool_accepts_input(func)
    if not accepts_input and (arguments or model.model_fields):
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid handler for {tool_name}: zero-argument handlers require an empty InputModel and no arguments"
        )
    args = (validated,) if accepts_input else ()

    if asyncio.iscoroutinefunction(func):
        return await func(*args)
    return func(*args)
