"""
Custom tools utilities for adapters.

Provides helper functions to convert Pydantic models to tool schemas
and execute custom tools with validation.
"""

from __future__ import annotations

import inspect
import logging
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import GenerateJsonSchema

from band.core.exceptions import InvalidToolArgumentsError
from band.core.turn import Turn
from band.runtime.tools.registry import ALL_TOOL_NAMES
from band.runtime.tools.schema import is_failed_tool_output
from band.runtime.tools.types import TurnEffect

logger = logging.getLogger(__name__)

# Type alias for custom tool definition: (InputModel, callable)
CustomToolDef = tuple[type[BaseModel], Callable[..., Any]]

_Handler = TypeVar("_Handler", bound=Callable[..., Any])


def declares_turn_effect(effect: TurnEffect) -> Callable[[_Handler], _Handler]:
    """Declare what a custom tool's successful call does to the turn's reply.

    ``ACT``: it did real work, so the turn is complete without a reply.
    ``REPLY``: it delivers the answer itself, so no fallback text is relayed.
    ``DECLINE``: silence is the answer, so no fallback text is relayed either.
    An undeclared custom tool counts as ``OBSERVE``: the turn still owes a reply.
    """

    def declare(handler: _Handler) -> _Handler:
        handler.band_effect = effect  # type: ignore[attr-defined]
        return handler

    return declare


def declared_effect(tool: Any) -> TurnEffect | None:
    """The turn effect a custom tool declares, or ``None`` when it declares none.

    Read off the **handler function** (``declares_turn_effect``). The older
    ``band_terminal = True`` marker is the shorthand for ``TurnEffect.ACT``.
    """
    if (effect := getattr(tool, "band_effect", None)) is not None:
        return effect
    return TurnEffect.ACT if getattr(tool, "band_terminal", False) else None


def declared_effects(
    named_handlers: Iterable[tuple[str, Any]],
) -> dict[str, TurnEffect]:
    """The declared turn effect per tool name; undeclared tools are omitted."""
    return {
        name: effect
        for name, handler in named_handlers
        if (effect := declared_effect(handler)) is not None
    }


def custom_tool_effects(tools: Iterable[CustomToolDef]) -> dict[str, TurnEffect]:
    """The declared turn effect per ``CustomToolDef`` name."""
    return declared_effects(
        (get_custom_tool_name(input_model), handler) for input_model, handler in tools
    )


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
    name = name.removesuffix("Input")  # Remove "Input" suffix
    return name.lower()


def reject_conflicting_tool_names(names: Iterable[str]) -> None:
    """Refuse custom tool names that would replace another tool.

    For a framework whose tool registry is last-wins, a name shadowing a Band
    platform tool or another custom tool silently replaces it.
    """
    counts = Counter(names)
    if shadowed := sorted(name for name in counts if name in ALL_TOOL_NAMES):
        raise ValueError(f"Custom tools may not shadow Band platform tools: {shadowed}")
    if duplicated := sorted(name for name, count in counts.items() if count > 1):
        raise ValueError(f"Custom tool names must be unique: {duplicated}")


def custom_tool_to_openai_schema(
    input_model: type[BaseModel],
    *,
    schema_generator: type[GenerateJsonSchema] | None = None,
) -> dict[str, Any]:
    """
    Convert Pydantic model to OpenAI function schema.

    Args:
        input_model: Pydantic model class defining tool input
        schema_generator: Optional model-schema generator for a framework's metadata.

    Returns:
        OpenAI-compatible tool schema with type="function"
    """
    schema_options: dict[str, Any] = (
        {} if schema_generator is None else {"schema_generator": schema_generator}
    )
    schema = input_model.model_json_schema(**schema_options)
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


def format_validation_error(exc: ValidationError) -> str:
    """Format a pydantic ValidationError as an LLM-readable field list.

    Model-level validators report an empty ``loc``, so the field name
    falls back to "unknown" instead of raising IndexError.
    """
    return "; ".join(
        f"{'.'.join(str(x) for x in err['loc']) if err.get('loc') else 'unknown'}: {err['msg']}"
        for err in exc.errors()
    )


def _custom_tool_accepts_input(func: Callable[..., Any]) -> bool:
    try:
        return len(inspect.signature(func).parameters) > 0
    except (TypeError, ValueError):
        return True


async def execute_custom_tool(
    tool: CustomToolDef,
    arguments: dict[str, Any],
    *,
    turn: Turn | None,
    strict: bool | None = None,
) -> Any:
    """
    Execute custom tool with Pydantic validation.

    Args:
        tool: (InputModel, callable) tuple
        arguments: Raw arguments dict from LLM
        turn: The turn to record the tool's declared effect on, or ``None``
            when the tool is not bound to a room, so it cannot settle a turn.
        strict: Overrides the input model's own strictness; ``None`` keeps it.

    Returns:
        Tool execution result

    Raises:
        InvalidToolArgumentsError: If arguments don't match InputModel schema
            (formatted for LLM)
        Exception: Any exception from tool function (for adapter to catch)
    """
    model, func = tool

    # Validate arguments, format errors for LLM readability
    try:
        validated = model.model_validate(arguments, strict=strict)
    except ValidationError as e:
        tool_name = get_custom_tool_name(model)
        raise InvalidToolArgumentsError(
            f"Invalid arguments for {tool_name}: {format_validation_error(e)}"
        ) from e

    if not _custom_tool_accepts_input(func) and arguments:
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid handler for {tool_name}: zero-argument handlers require an empty InputModel and no arguments"
        )
    return await invoke_validated_custom_tool(tool, validated, turn=turn)


async def invoke_validated_custom_tool(
    tool: CustomToolDef,
    validated: Any,
    *,
    turn: Turn | None,
) -> Any:
    """
    Execute a custom tool whose arguments are already a validated InputModel
    instance — the post-validation half of :func:`execute_custom_tool`.

    For callers whose framework has already constructed the InputModel (e.g.
    pydantic-ai validates tool args natively): re-serializing the instance to
    a dict and re-validating would break models using field aliases, so the
    instance is passed through as-is. Async/zero-argument handler semantics
    match :func:`execute_custom_tool` exactly, and so does ``turn``.
    """
    model, func = tool

    accepts_input = _custom_tool_accepts_input(func)
    if not accepts_input and model.model_fields:
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid handler for {tool_name}: zero-argument handlers require an empty InputModel and no arguments"
        )
    args = (validated,) if accepts_input else ()

    # Call the handler, then await if it produced an awaitable. This covers plain
    # sync/async functions AND callable objects with an async __call__ (for which
    # asyncio.iscoroutinefunction returns False, so a bare check would leak the
    # coroutine unawaited).
    result = func(*args)
    if inspect.isawaitable(result):
        result = await result
    if turn is not None and not is_failed_tool_output(result):
        turn.record(declared_effect(func) or TurnEffect.OBSERVE)
    return result
