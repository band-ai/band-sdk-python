"""
Custom tools utilities for adapters.

Provides helper functions to convert Pydantic models to tool schemas
and execute custom tools with validation.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from band.runtime.execution import ExecutionContext

logger = logging.getLogger(__name__)

# Type alias for custom tool definition: (InputModel, callable)
CustomToolDef = tuple[type[BaseModel], Callable[..., Any]]


def ctx_from_tools(tools: Any) -> "ExecutionContext | None":
    """Best-effort ``ExecutionContext`` behind an AgentTools instance.

    ``AgentTools.from_context`` stashes the per-execution context on ``_ctx``;
    every custom-tool call site threads it from there into
    :func:`execute_custom_tool` (INT-994). Deliberately defensive: adapter
    tests drive these paths with ``FakeAgentTools`` (no ``_ctx``), plain
    mocks, or ``None`` — all of those mean "no context available", never an
    error.
    """
    return getattr(tools, "_ctx", None)


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


def is_marked_terminal(tool: Any) -> bool:
    """Whether a custom tool opts in as a *terminal* action.

    A custom tool declares itself terminal by setting ``band_terminal = True`` on
    its **handler function** — both adapters read the flag off the handler (crewai
    from the ``(input_model, handler)`` tuple; pydantic-ai from the registered
    function). Terminal custom tools let an empty final model response be treated as
    benign (the tool completed the turn); undeclared custom tools do not (fail-loud
    — see ``runtime.tools.is_terminal_success``).
    """
    return bool(getattr(tool, "band_terminal", False))


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


def format_validation_error(exc: ValidationError) -> str:
    """Format a pydantic ValidationError as an LLM-readable field list.

    Model-level validators report an empty ``loc``, so the field name
    falls back to "unknown" instead of raising IndexError.
    """
    return "; ".join(
        f"{'.'.join(str(x) for x in err['loc']) if err.get('loc') else 'unknown'}: {err['msg']}"
        for err in exc.errors()
    )


# Handler positional arity, memoized: signatures are immutable, and the tool
# loop re-inspects the same handler on every call otherwise. Keyed by the
# callable itself; an unhashable callable (custom __eq__ without __hash__)
# simply skips the cache.
_HANDLER_ARITY_CACHE: dict[Callable[..., Any], int] = {}


def _custom_tool_positional_arity(func: Callable[..., Any]) -> int:
    """How many positional parameters the handler declares, capped at 2.

    0 = zero-argument handler (empty InputModel required); 1 = the classic
    ``handler(args)`` shape; 2 = the INT-994 opt-in ``handler(args, ctx)``
    that also receives the ``ExecutionContext`` (or ``None`` when the call
    site has none). Only POSITIONAL_ONLY / POSITIONAL_OR_KEYWORD parameters
    count — ``*args`` is NOT an opt-in, so pre-INT-994 handlers keep their
    exact call shape. Uninspectable callables fall back to the classic shape.
    """
    try:
        return _HANDLER_ARITY_CACHE[func]
    except KeyError:
        pass
    except TypeError:  # unhashable callable: inspect without caching
        return _inspect_positional_arity(func)

    arity = _inspect_positional_arity(func)
    _HANDLER_ARITY_CACHE[func] = arity
    return arity


def _inspect_positional_arity(func: Callable[..., Any]) -> int:
    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return 1
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return min(len(positional), 2)


def _custom_tool_accepts_input(func: Callable[..., Any]) -> bool:
    return _custom_tool_positional_arity(func) > 0


def custom_tool_accepts_ctx(func: Callable[..., Any]) -> bool:
    """Whether a handler opted in to the INT-994 second (ctx) parameter.

    Adapters whose framework owns the call chain (pydantic-ai's RunContext
    registration) use this to pick the wrapper shape once, at conversion time.
    """
    return _custom_tool_positional_arity(func) >= 2


async def execute_custom_tool(
    tool: CustomToolDef,
    arguments: dict[str, Any],
    *,
    ctx: "ExecutionContext | None" = None,
) -> Any:
    """
    Execute custom tool with Pydantic validation.

    Args:
        tool: (InputModel, callable) tuple
        arguments: Raw arguments dict from LLM
        ctx: Optional per-execution context (INT-994). Delivered as the
            handler's second positional argument when — and only when — the
            handler declares one; zero-arg and one-arg handlers are called
            exactly as before.

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
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid arguments for {tool_name}: {format_validation_error(e)}"
        ) from e

    if not _custom_tool_accepts_input(func) and arguments:
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid handler for {tool_name}: zero-argument handlers require an empty InputModel and no arguments"
        )
    return await invoke_validated_custom_tool(tool, validated, ctx=ctx)


async def invoke_validated_custom_tool(
    tool: CustomToolDef,
    validated: Any,
    *,
    ctx: "ExecutionContext | None" = None,
) -> Any:
    """
    Execute a custom tool whose arguments are already a validated InputModel
    instance — the post-validation half of :func:`execute_custom_tool`.

    For callers whose framework has already constructed the InputModel (e.g.
    pydantic-ai validates tool args natively): re-serializing the instance to
    a dict and re-validating would break models using field aliases, so the
    instance is passed through as-is. Async/zero-argument handler and ``ctx``
    opt-in semantics match :func:`execute_custom_tool` exactly.
    """
    model, func = tool

    arity = _custom_tool_positional_arity(func)
    if arity == 0 and model.model_fields:
        tool_name = get_custom_tool_name(model)
        raise ValueError(
            f"Invalid handler for {tool_name}: zero-argument handlers require an empty InputModel and no arguments"
        )
    if arity == 0:
        args: tuple[Any, ...] = ()
    elif arity == 1:
        args = (validated,)
    else:
        args = (validated, ctx)

    # Call the handler, then await if it produced an awaitable. This covers plain
    # sync/async functions AND callable objects with an async __call__ (for which
    # asyncio.iscoroutinefunction returns False, so a bare check would leak the
    # coroutine unawaited).
    result = func(*args)
    if inspect.isawaitable(result):
        return await result
    return result
