"""Rendering a tool's master input model into the shapes adapters consume:
LLM-facing description/``Args:`` text, a framework's ``args_schema``, a
function docstring (``@platform_tool``), argument validation, and the
dispatch-boundary result types (``ToolCallOutcome``, ``serialize_tool_result``).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError, create_model

from band.runtime.tools.registry import TOOL_MODELS


def resolve_tool_model(name: str) -> type[BaseModel] | None:
    """Resolve a tool name to its master input model.

    Accepts the deprecated unprefixed spelling too, so every consumer sees one
    resolution rule. Warning about the deprecated form is the caller's job —
    this stays quiet so it can be used for lookups that aren't user-facing.
    """
    return TOOL_MODELS.get(name) or TOOL_MODELS.get(f"band_{name}")


def get_tool_description(name: str) -> str:
    """
    Get the LLM-optimized description for a tool.

    Use this to get consistent tool descriptions across all adapters.
    Descriptions are sourced from the Pydantic model docstrings.

    Args:
        name: Tool name (e.g., "band_send_message", "band_lookup_peers")
              Also accepts unprefixed names for backwards compatibility (deprecated).

    Returns:
        Tool description string
    """
    model = resolve_tool_model(name)
    if model is None or not model.__doc__:
        return f"Execute {name}"

    if name not in TOOL_MODELS:
        warnings.warn(
            f"Tool name '{name}' is deprecated. Use 'band_{name}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    return model.__doc__


def format_arg_doc(name: str, description: str) -> str:
    """Render one Google-style ``Args:`` entry.

    Continuation lines are indented past the argument name so a multi-line
    ``Field(description=...)`` stays part of that argument — flush-left
    continuations end the entry as far as a docstring parser is concerned.
    """
    head, *rest = description.strip().splitlines()
    return "\n".join(
        [f"    {name}: {head}", *(f"        {line.strip()}" for line in rest)]
    )


def get_tool_docstring_with_args(name: str) -> str:
    """Return the tool description plus a Google-style ``Args:`` section.

    Both halves come from the master model: the class docstring and each
    field's ``Field(description=...)``. Frameworks that build their schema by
    parsing a Python function's docstring (pydantic-ai via griffe) only see
    per-argument text if it appears under ``Args:``, so this renders it there
    rather than having each adapter retype it.
    """
    description = get_tool_description(name)
    model = resolve_tool_model(name)
    if model is None:
        return description

    arg_lines = [
        format_arg_doc(field_name, field.description)
        for field_name, field in model.model_fields.items()
        if field.description and field.description.strip()
    ]
    if not arg_lines:
        return description
    return f"{description.rstrip()}\n\nArgs:\n" + "\n".join(arg_lines)


ToolFunc = TypeVar("ToolFunc", bound=Callable[..., Any])


def platform_tool(fn: ToolFunc) -> ToolFunc:
    """Give a tool function the master description and ``Args:`` section.

    For frameworks that derive their schema from the function docstring. Reads
    ``fn.__name__`` rather than taking a tool name argument — the function is
    always named after the tool it registers (frameworks that key a tool by
    its function name, like pydantic-ai, require this already), so there is
    nowhere left to retype that name, let alone the description.
    """
    fn.__doc__ = get_tool_docstring_with_args(fn.__name__)
    return fn


def platform_args_schema(
    name: str,
    *,
    validators: dict[str, Any] | None = None,
) -> type[BaseModel]:
    """Return the master input model for ``name`` as a framework args schema.

    ``validators`` layers extra pydantic validators onto a subclass for
    frameworks whose tool-calling layer emits a value the master model is too
    strict to parse. There is deliberately no field or description override:
    an adapter needing different *text* has a modeling problem to fix on the
    master, not a formatting one to patch locally.
    """
    model = resolve_tool_model(name)
    if model is None:
        raise KeyError(name)
    if not validators:
        return model
    return create_model(
        f"{model.__name__}Adapted",
        __base__=model,
        # create_model does not inherit the base docstring, and that docstring
        # is the tool description every adapter reads.
        __doc__=model.__doc__,
        __validators__=validators,
    )


def format_tool_validation_error(tool_name: str, error: ValidationError) -> str:
    """Format Pydantic validation errors for LLM-readable tool feedback."""
    errors = [
        f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
        for err in error.errors()
    ]
    return f"Invalid arguments for {tool_name}: {', '.join(errors)}"


def validate_tool_arguments(
    tool_name: str,
    input_model: type[BaseModel],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Validate tool arguments and return a normalized kwargs dictionary."""
    try:
        validated = input_model.model_validate(arguments)
    except ValidationError as error:
        raise ValueError(format_tool_validation_error(tool_name, error)) from error

    return validated.model_dump(exclude_none=True)


@dataclass(frozen=True)
class ToolCallOutcome:
    """Structured result of :meth:`AgentTools.execute_tool_call_structured`.

    ``value`` is the JSON-serializable payload handed to the LLM (the
    success result, or an error string on failure so the model can still
    react). ``ok`` is the machine-readable success flag and
    ``error_message`` the human-readable failure detail. Together they let
    callers branch on success/failure without parsing ``value`` — e.g. the
    Slack plan-progress UI marks a task ✅/❌ from ``ok`` rather than
    sniffing the error string's prefix.
    """

    value: Any
    ok: bool
    error_message: str | None = None


def serialize_tool_result(result: Any) -> Any:
    """Serialize Pydantic tool results to dicts at the adapter boundary.

    The single definition of how a tool method's return value (a Fern model,
    a list of models, or an already-plain value) becomes the JSON-serializable
    payload adapters receive. Test fakes that mirror the dispatch boundary
    (e.g. the baseline ``BaselineTools``) must use this same helper so their
    output shape cannot drift from the real one.
    """
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, list):
        return [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in result
        ]
    return result
