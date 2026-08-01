"""FunctionTool artifacts for portable custom tools (Phase 3A)."""

from __future__ import annotations

import inspect
import types
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field, create_model
from band.core.bases import FrozenModel

from band.core.exceptions import DuplicateToolError
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
    is_marked_terminal,
)

_BAND_FUNCTION_TOOL_ATTR = "__band_function_tool__"
_UNION_ORIGINS = (Union, types.UnionType)


@dataclass(frozen=True)
class ToolContext:
    """Runtime context injected into tools that declare a ``ToolContext`` param.

    A parameter is treated as context when it is annotated as ``ToolContext``
    (any position), or when it is an unannotated ``ctx`` / ``context`` name.
    Context params are excluded from the JSON schema and supplied at execution
    time via :meth:`FunctionTool.execute` ``context=`` (injected by name).
    """

    tools: Any = None


class ToolSpec(FrozenModel):
    """Provider-neutral tool specification derived from a parameter model."""

    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, Any]


def tool_spec_to_openai_schema(spec: ToolSpec) -> dict[str, Any]:
    """Convert a :class:`ToolSpec` to an OpenAI function tool schema."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def tool_spec_to_anthropic_schema(spec: ToolSpec) -> dict[str, Any]:
    """Convert a :class:`ToolSpec` to an Anthropic tool schema."""
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.parameters,
    }


def _is_union_origin(origin: Any) -> bool:
    return origin in _UNION_ORIGINS


def _is_tool_context_type(annotation: Any) -> bool:
    if annotation is ToolContext:
        return True
    origin = get_origin(annotation)
    if _is_union_origin(origin):
        return any(arg is ToolContext for arg in get_args(annotation))
    return False


def _is_tool_context_param(name: str, annotation: Any) -> bool:
    if annotation is inspect.Parameter.empty:
        return name in ("ctx", "context")
    return _is_tool_context_type(annotation)


def _context_param_name(handler: Callable[..., Any]) -> str | None:
    sig = inspect.signature(handler)
    try:
        hints = get_type_hints(handler, include_extras=True)
    except (NameError, TypeError):
        hints = {}
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        annotation = hints.get(param_name, param.annotation)
        if _is_tool_context_param(param_name, annotation):
            return param_name
    return None


def _is_supported_annotation(annotation: Any) -> bool:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return True

    origin = get_origin(annotation)
    if origin is None:
        if annotation in (str, int, float, bool):
            return True
        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            return True
        if inspect.isclass(annotation) and issubclass(annotation, Enum):
            return True
        return False

    if origin is Literal:
        return True

    if _is_union_origin(origin):
        args = get_args(annotation)
        if type(None) in args:
            inner = [arg for arg in args if arg is not type(None)]
            return len(inner) == 1 and _is_supported_annotation(inner[0])
        return all(_is_supported_annotation(arg) for arg in args)

    if origin is list:
        args = get_args(annotation)
        return len(args) == 1 and _is_supported_annotation(args[0])

    if origin is dict:
        args = get_args(annotation)
        if len(args) != 2:
            return False
        key_type, value_type = args
        return key_type is str and _is_supported_annotation(value_type)

    if inspect.isclass(origin) and issubclass(origin, Enum):
        return True

    return False


def _model_name_for_tool(tool_name: str) -> str:
    if not tool_name:
        return "ToolInput"
    cleaned = "".join(
        part.capitalize() for part in tool_name.replace("-", "_").split("_")
    )
    if not cleaned:
        return "ToolInput"
    return f"{cleaned}Input"


def _stamp_parameters_model(
    parameters_model: type[BaseModel],
    *,
    name: str,
    description: str,
) -> type[BaseModel]:
    """Align model metadata with ``FunctionTool`` so legacy CustomToolDef paths match."""
    expected = _model_name_for_tool(name)
    parameters_model.__name__ = expected
    parameters_model.__qualname__ = expected
    parameters_model.__doc__ = description
    # Preserve the canonical tool name for get_custom_tool_name (class-name
    # derivation lowercases and drops underscores: MyCalcInput -> "mycalc").
    parameters_model.__band_tool_name__ = name  # type: ignore[attr-defined]
    return parameters_model


def _build_parameters_model(
    fn: Callable[..., Any],
    *,
    tool_name: str,
    lenient: bool,
) -> tuple[type[BaseModel], bool]:
    """Build a dynamic input model from ``fn``'s signature."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except (NameError, TypeError):
        hints = {}

    fields: dict[str, tuple[Any, Any]] = {}
    needs_context = False

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        if param.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            raise ValueError(
                f"Unsupported parameter kind for {param_name!r} on "
                f"{fn.__name__}: {param.kind.description}. "
                "Band tools require named parameters."
            )

        annotation = hints.get(param_name, param.annotation)
        if _is_tool_context_param(param_name, annotation):
            needs_context = True
            continue
        if annotation is inspect.Parameter.empty:
            annotation = Any

        if not _is_supported_annotation(annotation):
            if lenient:
                warnings.warn(
                    f"Skipping parameter {param_name!r} on {fn.__name__}: "
                    f"unsupported annotation {annotation!r}",
                    UserWarning,
                    stacklevel=4,
                )
                continue
            raise ValueError(
                f"Unsupported annotation for parameter {param_name!r} on "
                f"{fn.__name__}: {annotation!r}. Supported types: str, int, float, "
                "bool, list[T], dict[str, T], Optional/Union, Literal, Enum, and "
                "BaseModel subclasses."
            )

        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model_name = _model_name_for_tool(tool_name)
    parameters_model = create_model(model_name, **fields)  # type: ignore[call-overload]
    return parameters_model, needs_context


async def _invoke_handler(
    handler: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    result = handler(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass(frozen=True)
class FunctionTool:
    """A typed custom tool with schema generation and portable execution."""

    name: str
    description: str
    parameters_model: type[BaseModel]
    handler: Callable[..., Any]
    terminal: bool = False
    lenient: bool = False
    needs_context: bool = False
    handler_accepts_model: bool = False
    native_callable: Callable[..., Any] | None = None

    def spec(self) -> ToolSpec:
        schema = dict(self.parameters_model.model_json_schema())
        schema.pop("title", None)
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=schema,
        )

    def as_custom_tool_def(
        self,
        *,
        context: ToolContext | None = None,
    ) -> CustomToolDef:
        underlying = self.handler
        context_name = _context_param_name(underlying) if self.needs_context else None

        if self.needs_context or self.terminal or not self.handler_accepts_model:
            ctx = context or ToolContext()

            async def wrapped(validated: BaseModel) -> Any:
                if self.handler_accepts_model:
                    if self.needs_context and context_name is not None:
                        return await _invoke_handler(
                            underlying,
                            validated,
                            **{context_name: ctx},
                        )
                    return await _invoke_handler(underlying, validated)
                kwargs = validated.model_dump()
                if self.needs_context and context_name is not None:
                    return await _invoke_handler(
                        underlying,
                        **{context_name: ctx, **kwargs},
                    )
                return await _invoke_handler(underlying, **kwargs)

            if self.terminal:
                wrapped.band_terminal = True  # type: ignore[attr-defined]
            return (self.parameters_model, wrapped)

        return (self.parameters_model, underlying)

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: ToolContext | None = None,
    ) -> Any:
        if self.native_callable is not None:
            raise TypeError(
                f"FunctionTool {self.name!r} wraps a native callable; "
                "execute it through the framework adapter instead."
            )
        return await execute_custom_tool(
            self.as_custom_tool_def(context=context),
            arguments,
        )

    @classmethod
    def from_custom_tool_def(
        cls,
        tool: CustomToolDef,
        *,
        terminal: bool | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> FunctionTool:
        model, handler = tool
        resolved_name = name or get_custom_tool_name(model)
        resolved_description = (
            description if description is not None else (model.__doc__ or "")
        )
        resolved_terminal = (
            terminal if terminal is not None else is_marked_terminal(handler)
        )
        return cls(
            name=resolved_name,
            description=resolved_description,
            parameters_model=model,
            handler=handler,
            terminal=resolved_terminal,
            handler_accepts_model=True,
        )

    @classmethod
    def from_callable(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        terminal: bool = False,
        lenient: bool = False,
    ) -> FunctionTool:
        tool_name = name or fn.__name__
        parameters_model, needs_context = _build_parameters_model(
            fn, tool_name=tool_name, lenient=lenient
        )
        description = (fn.__doc__ or "").strip()
        parameters_model = _stamp_parameters_model(
            parameters_model, name=tool_name, description=description
        )
        return cls(
            name=tool_name,
            description=description,
            parameters_model=parameters_model,
            handler=fn,
            terminal=terminal,
            lenient=lenient,
            needs_context=needs_context,
        )


def _function_tool_from_callable(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    terminal: bool = False,
    lenient: bool = False,
) -> FunctionTool:
    return FunctionTool.from_callable(
        fn,
        name=name,
        terminal=terminal,
        lenient=lenient,
    )


def native_tool(fn: Callable[..., Any]) -> FunctionTool:
    """Wrap ``fn`` for a framework that derives its own tool schema.

    The parameters model is a placeholder: the adapter passes
    ``native_callable`` straight to its framework, which reads the real
    signature itself. That is the only way a framework's own annotation
    support — anything Band's schema builder does not model — keeps working.
    """
    return FunctionTool(
        name=fn.__name__,
        description=(fn.__doc__ or "").strip(),
        parameters_model=create_model(f"{fn.__name__.title()}Input"),
        handler=fn,
        native_callable=fn,
    )


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    terminal: bool = False,
    lenient: bool = False,
) -> Callable[..., Any] | FunctionTool:
    """Decorator that builds a :class:`FunctionTool` from a typed function."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        function_tool = _function_tool_from_callable(
            fn,
            name=name,
            terminal=terminal,
            lenient=lenient,
        )
        setattr(fn, _BAND_FUNCTION_TOOL_ATTR, function_tool)
        return fn

    if func is not None:
        return decorator(func)
    return decorator


def normalize_additional_tools(
    tools: Sequence[FunctionTool | Callable[..., Any]] | None,
    *,
    framework_derives_schemas: bool = False,
) -> list[FunctionTool]:
    """Normalize adapter ``additional_tools`` to a deduplicated ``FunctionTool`` list.

    Set ``framework_derives_schemas`` for an adapter whose framework builds
    tool schemas from the callable itself (pydantic-ai). Band then leaves
    plain callables alone instead of insisting on annotations only its own
    schema builder understands. An ``@tool``-decorated callable always keeps
    the ``FunctionTool`` it was decorated with.
    """
    if tools is None:
        return []

    normalized: list[FunctionTool] = []
    seen: set[str] = set()

    for item in tools:
        if isinstance(item, FunctionTool):
            function_tool = item
        elif isinstance(item, tuple):
            raise TypeError(
                "Bare (Model, handler) tuples are no longer accepted in "
                "additional_tools; use FunctionTool.from_custom_tool_def(...) "
                "or an @tool-decorated callable."
            )
        elif callable(item):
            existing = getattr(item, _BAND_FUNCTION_TOOL_ATTR, None)
            if existing is not None:
                function_tool = existing
            elif framework_derives_schemas:
                function_tool = native_tool(item)
            else:
                decorated = tool(item)
                function_tool = getattr(decorated, _BAND_FUNCTION_TOOL_ATTR)
        else:
            raise TypeError(
                f"Unsupported additional_tools entry {item!r}; expected "
                "FunctionTool or callable."
            )

        if function_tool.name in seen:
            raise DuplicateToolError(
                f"Duplicate tool name {function_tool.name!r} in additional_tools."
            )
        seen.add(function_tool.name)
        normalized.append(function_tool)

    return normalized
