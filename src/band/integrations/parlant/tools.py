"""
Parlant tool definitions that wrap Band AgentTools.

These tools are defined at server startup and use a session-keyed registry
to access the current room's tools during execution.

NOTE: We intentionally do NOT use `from __future__ import annotations` here
because Parlant's @p.tool decorator checks annotation types at runtime.
"""

from dataclasses import dataclass
import inspect
import json
import logging
from types import UnionType
from typing import Any, Optional, Union, get_args, get_origin
import uuid
import warnings

from pydantic_core import PydanticUndefined

from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, Capability
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
)
from band.runtime.tools import (
    CONTACT_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    ToolDefinition,
    get_tool_description,
    iter_tool_definitions,
)

logger = logging.getLogger(__name__)


@dataclass
class _SessionContext:
    tools: AgentToolsProtocol
    message_sent: bool = False
    emit_execution: bool = False


# Session-keyed registry to hold tools and delivery state.
# This approach works across async contexts (unlike ContextVar).
_session_contexts: dict[str, _SessionContext] = {}

# Platform tools already create user-visible Band effects directly, so they are
# not re-reported as execution events (that would double-count the send).
_SILENT_REPORTING_TOOLS = frozenset({"band_send_message", "band_send_event"})

_ERROR_PREFIXES = (
    "Error",
    "Invalid arguments",
    "Unknown tool",
)


def set_session_tools(
    session_id: str,
    tools: Optional[AgentToolsProtocol],
    *,
    emit_execution: bool = False,
) -> None:
    """Set the tools for a specific Parlant session.

    Args:
        session_id: Parlant session ID the tools belong to.
        tools: Room AgentTools, or ``None`` to clear the session.
        emit_execution: When true, generated tool wrappers report each call as
            ``tool_call``/``tool_result`` Band events in real time, interleaved
            with the actual side effects.
    """
    if tools is None:
        _session_contexts.pop(session_id, None)
    else:
        _session_contexts[session_id] = _SessionContext(
            tools=tools,
            emit_execution=emit_execution,
        )
    logger.debug("Set tools for session %s: %s", session_id, tools is not None)


def _get_session_context(session_id: str) -> Optional[_SessionContext]:
    context = _session_contexts.get(session_id)
    logger.debug(
        "Get context for session_id=%s: found=%s, available_sessions=%s",
        session_id,
        context is not None,
        list(_session_contexts.keys()),
    )
    return context


def get_session_tools(session_id: str) -> Optional[AgentToolsProtocol]:
    """Get the tools for a specific Parlant session."""
    context = _get_session_context(session_id)
    return context.tools if context else None


def mark_message_sent(session_id: str) -> None:
    """Mark that a message was sent via the send_message tool for this session."""
    if context := _get_session_context(session_id):
        context.message_sent = True
    logger.debug("Marked message sent for session %s", session_id)


def was_message_sent(session_id: str) -> bool:
    """Check if a message was sent via the send_message tool."""
    context = _get_session_context(session_id)
    return bool(context and context.message_sent)


# Keep old API for backwards compatibility (deprecated).
def set_current_tools(tools: Optional[AgentToolsProtocol]) -> None:
    """Deprecated: Use set_session_tools instead."""
    warnings.warn(
        "set_current_tools is deprecated, use set_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )


def get_current_tools() -> Optional[AgentToolsProtocol]:
    """Deprecated: Use get_session_tools instead."""
    warnings.warn(
        "get_current_tools is deprecated, use get_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return None


def _tool_name(entry: Any) -> str:
    return str(entry.tool.name)


def _tool_category(entry: Any) -> str | None:
    name = _tool_name(entry)
    if name in MEMORY_TOOL_NAMES:
        return "memory"
    if name in CONTACT_TOOL_NAMES:
        return "contacts"
    return "chat"


def _is_error_result(result: Any) -> bool:
    return isinstance(result, str) and result.startswith(_ERROR_PREFIXES)


def _dump_data(result: Any) -> Any:
    """Normalize Fern/Pydantic models for JSON-friendly tool output."""
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, list):
        return [_dump_data(item) for item in result]
    if isinstance(result, dict):
        return {key: _dump_data(value) for key, value in result.items()}
    return result


def _tool_result_data(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(_dump_data(result), default=str)


def _is_union(annotation: Any) -> bool:
    return get_origin(annotation) in (Union, UnionType)


def _is_optional(annotation: Any) -> bool:
    return _is_union(annotation) and type(None) in get_args(annotation)


def _optional_inner(annotation: Any) -> Any:
    return next(arg for arg in get_args(annotation) if arg is not type(None))


def _is_literal(annotation: Any) -> bool:
    return str(get_origin(annotation)) == "typing.Literal"


def _is_dict_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is dict:
        return True
    if _is_optional(annotation):
        return _is_dict_annotation(_optional_inner(annotation))
    return False


def _parlant_supported_annotation(annotation: Any) -> Any:
    """Map Pydantic field annotations to Parlant-supported tool annotations."""
    if _is_optional(annotation):
        inner = _parlant_supported_annotation(_optional_inner(annotation))
        return inner | None
    if _is_literal(annotation) or annotation is Any:
        return str
    if _is_dict_annotation(annotation):
        return str
    return annotation


def _coerce_parlant_arguments(
    tool_name: str,
    input_model: type[Any],
    arguments: dict[str, Any],
) -> dict[str, Any] | str:
    """Convert Parlant-friendly values back to canonical tool arguments."""
    coerced = dict(arguments)
    for name, field in input_model.model_fields.items():
        if name not in coerced or coerced[name] in (None, ""):
            continue
        if not _is_dict_annotation(field.annotation):
            continue
        if not isinstance(coerced[name], str):
            continue
        try:
            coerced[name] = json.loads(coerced[name])
        except json.JSONDecodeError as error:
            return (
                f"Invalid arguments for {tool_name}: {name} must be valid JSON: {error}"
            )
    return coerced


def _signature_for_input_model(
    input_model: type[Any],
    tool_context_type: type[Any],
    tool_result_type: type[Any],
) -> inspect.Signature:
    parameters = [
        inspect.Parameter(
            "context",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=tool_context_type,
        )
    ]

    for name, field in input_model.model_fields.items():
        default = inspect.Parameter.empty
        if not field.is_required():
            default = None if field.default is PydanticUndefined else field.default

        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=_parlant_supported_annotation(field.annotation),
            )
        )

    return inspect.Signature(
        parameters=parameters,
        return_annotation=tool_result_type,
    )


def _new_tool_call_id() -> str:
    return f"parlant-{uuid.uuid4().hex[:12]}"


async def _report_tool_call(
    session_context: _SessionContext,
    tool_name: str,
    arguments: Any,
    tool_call_id: str,
) -> None:
    """Emit a Band ``tool_call`` event in real time, before the tool runs."""
    if not session_context.emit_execution or tool_name in _SILENT_REPORTING_TOOLS:
        return
    try:
        await session_context.tools.send_event(
            content=json.dumps(
                {
                    "name": tool_name,
                    "args": _dump_data(arguments),
                    "tool_call_id": tool_call_id,
                },
                default=str,
            ),
            message_type="tool_call",
        )
    except Exception as error:
        logger.warning("Failed to report tool_call for %s: %s", tool_name, error)


async def _report_tool_result(
    session_context: _SessionContext,
    tool_name: str,
    output: Any,
    tool_call_id: str,
) -> None:
    """Emit a Band ``tool_result`` event in real time, after the tool runs."""
    if not session_context.emit_execution or tool_name in _SILENT_REPORTING_TOOLS:
        return
    try:
        await session_context.tools.send_event(
            content=json.dumps(
                {
                    "name": tool_name,
                    "output": _dump_data(output),
                    "tool_call_id": tool_call_id,
                },
                default=str,
            ),
            message_type="tool_result",
        )
    except Exception as error:
        logger.warning("Failed to report tool_result for %s: %s", tool_name, error)


def _create_builtin_parlant_tool_entry(
    definition: ToolDefinition,
    p: Any,
    tool_context_type: type[Any],
    tool_result_type: type[Any],
) -> Any:
    signature = _signature_for_input_model(
        definition.input_model,
        tool_context_type,
        tool_result_type,
    )

    async def wrapper(context: Any, *args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(context, *args, **kwargs)
        bound.apply_defaults()
        arguments = {
            key: value for key, value in bound.arguments.items() if key != "context"
        }
        session_id = str(context.session_id)
        session_context = _get_session_context(session_id)

        if not session_context:
            return tool_result_type(data="Error: No tools available in current context")

        coerced = _coerce_parlant_arguments(
            definition.name,
            definition.input_model,
            arguments,
        )
        if isinstance(coerced, str):
            return tool_result_type(data=coerced)

        tool_call_id = _new_tool_call_id()
        await _report_tool_call(session_context, definition.name, coerced, tool_call_id)

        try:
            result = await session_context.tools.execute_tool_call(
                definition.name,
                coerced,
            )
        except BandToolError as error:
            logger.error(
                "[Parlant Tool] Band tool error in %s: %s",
                definition.name,
                error,
                exc_info=True,
            )
            output = f"Error executing {definition.name}: {error}"
            await _report_tool_result(
                session_context, definition.name, output, tool_call_id
            )
            return tool_result_type(data=output)
        except Exception as error:
            logger.error(
                "[Parlant Tool] Unexpected error in %s: %s",
                definition.name,
                error,
                exc_info=True,
            )
            output = f"Error executing {definition.name}: {error}"
            await _report_tool_result(
                session_context, definition.name, output, tool_call_id
            )
            return tool_result_type(data=output)

        if definition.name == "band_send_message" and not _is_error_result(result):
            session_context.message_sent = True

        await _report_tool_result(
            session_context, definition.name, result, tool_call_id
        )
        output = _tool_result_data(result)
        return tool_result_type(data=output)

    wrapper.__name__ = definition.name
    wrapper.__qualname__ = definition.name
    wrapper.__doc__ = get_tool_description(definition.name)
    wrapper.__signature__ = signature  # type: ignore[attr-defined]

    return p.tool(name=definition.name)(wrapper)


def _create_custom_parlant_tool_entry(
    custom_tool: CustomToolDef,
    p: Any,
    tool_context_type: type[Any],
    tool_result_type: type[Any],
) -> Any:
    input_model, _handler = custom_tool
    tool_name = get_custom_tool_name(input_model)
    signature = _signature_for_input_model(
        input_model,
        tool_context_type,
        tool_result_type,
    )

    async def wrapper(context: Any, *args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(context, *args, **kwargs)
        bound.apply_defaults()
        arguments = {
            key: value for key, value in bound.arguments.items() if key != "context"
        }
        coerced = _coerce_parlant_arguments(tool_name, input_model, arguments)
        if isinstance(coerced, str):
            return tool_result_type(data=coerced)

        session_context = _get_session_context(str(context.session_id))
        tool_call_id = _new_tool_call_id()
        if session_context:
            await _report_tool_call(session_context, tool_name, coerced, tool_call_id)

        try:
            result = await execute_custom_tool(custom_tool, coerced)
        except ValueError as error:
            output = str(error)
            if session_context:
                await _report_tool_result(
                    session_context, tool_name, output, tool_call_id
                )
            return tool_result_type(data=output)
        except Exception as error:
            logger.error(
                "[Parlant Tool] Unexpected error in custom tool %s: %s",
                tool_name,
                error,
                exc_info=True,
            )
            output = f"Error executing {tool_name}: {error}"
            if session_context:
                await _report_tool_result(
                    session_context, tool_name, output, tool_call_id
                )
            return tool_result_type(data=output)

        if session_context:
            await _report_tool_result(session_context, tool_name, result, tool_call_id)
        output = _tool_result_data(result)
        return tool_result_type(data=output)

    wrapper.__name__ = tool_name
    wrapper.__qualname__ = tool_name
    wrapper.__doc__ = input_model.__doc__ or f"Execute {tool_name}"
    wrapper.__signature__ = signature  # type: ignore[attr-defined]

    return p.tool(name=tool_name)(wrapper)


def create_parlant_tools(
    features: AdapterFeatures | None = None,
    *,
    legacy_defaults: bool | None = None,
    additional_tools: list[CustomToolDef] | None = None,
) -> list[Any]:
    """Create Parlant tool definitions that wrap canonical Band tools.

    Args:
        features: Optional adapter features. Explicit features control contact
            and memory capability exposure.
        legacy_defaults: When true, preserve the historical direct-call default
            of exposing contact tools even when no explicit feature selection was
            provided. Defaults to true only when ``features`` is ``None``.
            Adapter code passes this based on whether the caller supplied
            ``features=``.
        additional_tools: CustomToolDef tuples to expose as native Parlant tools.

    Returns:
        List of Parlant ToolEntry objects.
    """
    try:
        import parlant.sdk as p  # type: ignore[missing-import]
        from parlant.core.tools import ToolContext, ToolResult  # type: ignore[missing-import]
    except ImportError:
        logger.warning("Parlant SDK not installed, skipping tool creation")
        return []

    feature_config = features or AdapterFeatures()
    use_legacy_defaults = (
        features is None if legacy_defaults is None else legacy_defaults
    )
    include_contacts = (
        True
        if use_legacy_defaults
        else Capability.CONTACTS in feature_config.capabilities
    )
    include_memory = Capability.MEMORY in feature_config.capabilities

    entries = [
        _create_builtin_parlant_tool_entry(definition, p, ToolContext, ToolResult)
        for definition in iter_tool_definitions(
            surface="agent",
            include_memory=include_memory,
            include_contacts=include_contacts,
        )
    ]
    entries.extend(
        _create_custom_parlant_tool_entry(tool, p, ToolContext, ToolResult)
        for tool in additional_tools or []
    )

    return filter_tool_schemas(
        entries,
        feature_config,
        get_name=_tool_name,
        get_category=_tool_category,
    )
