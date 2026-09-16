"""The built-in Band tools a Pydantic AI agent is given.

Sole owner of built-in tool selection, construction, validation, dispatch and
result normalization for the pydantic-ai door: ``PydanticAIAdapter`` builds its
agent around whatever :func:`build_band_pydantic_ai_tools` returns and never
registers a platform tool itself.

Each tool is a native ``pydantic_ai.Tool`` built from the registry's
``ToolDefinition`` plus the master input model's schema, so a tool's name,
description, argument text, capability gate and dispatch target have exactly
one definition and no hand-written wrapper to drift from it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.messages import BinaryContent

from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures
from band.runtime.tools import (
    BandTool,
    ToolDefinition,
    decode_image_block,
    get_band_tool_category,
    get_tool_description,
    is_image_passthrough_result,
    iter_tool_definitions,
    platform_args_schema,
    serialize_tool_result,
    validate_tool_arguments,
)

logger = logging.getLogger(__name__)


def build_band_pydantic_ai_tools(
    features: AdapterFeatures,
) -> list[Tool[AgentToolsProtocol]]:
    """The native pydantic-ai tools for the built-in Band surface ``features`` allows."""
    return [_build_tool(definition) for definition in _selected_definitions(features)]


def _selected_definitions(features: AdapterFeatures) -> list[ToolDefinition]:
    """The agent-surface definitions left after the capability gate and filters."""
    return filter_tool_schemas(
        iter_tool_definitions(capabilities=features.capabilities),
        features,
        get_name=lambda definition: definition.name,
        get_category=lambda definition: get_band_tool_category(definition.name),
    )


def _build_tool(definition: ToolDefinition) -> Tool[AgentToolsProtocol]:
    """Build one native pydantic-ai tool for ``definition``.

    ``Tool.from_schema`` is the public way to hand pydantic-ai a ready-made
    JSON schema rather than a function for it to introspect — which is what
    lets one generic dispatcher stand in for a hand-written function per tool.
    """
    schema = platform_args_schema(definition.name)
    return Tool.from_schema(
        _dispatcher(definition, schema),
        # str(): ``ToolDefinition.name`` carries a ``BandTool`` member, and it
        # becomes a registry key pydantic-ai and its callers compare as a name.
        name=str(definition.name),
        description=get_tool_description(definition.name).strip(),
        json_schema=schema.model_json_schema(),
        takes_ctx=True,
        args_validator=_arguments_validator(definition, schema),
    )


def _validated_kwargs(
    definition: ToolDefinition,
    schema: type[BaseModel],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """``arguments`` as normalized kwargs, or a retry prompt for the model."""
    try:
        return validate_tool_arguments(definition.name, schema, arguments)
    except ValueError as error:
        raise ModelRetry(str(error)) from error


def _arguments_validator(
    definition: ToolDefinition, schema: type[BaseModel]
) -> Callable[..., None]:
    """pydantic-ai's pre-execution argument check for ``definition``.

    Without it nothing would validate at all: ``Tool.from_schema`` installs a
    pass-through schema validator, so the advertised JSON schema is only ever
    advice to the model. The dispatcher validates a second time because
    pydantic-ai discards whatever this returns and calls the tool with the
    model's raw arguments — this hook can only reject, never normalize.
    """

    def validate(_ctx: RunContext[AgentToolsProtocol], **arguments: Any) -> None:
        _validated_kwargs(definition, schema, arguments)

    return validate


def _resolve_method(
    deps: AgentToolsProtocol, definition: ToolDefinition
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """``definition.method_name`` bound on ``deps``, or an actionable error.

    A stale or typo'd registry entry should surface as this message, not a
    bare ``AttributeError`` from whichever tool call happens to hit it first.
    """
    method = getattr(deps, definition.method_name, None)
    if method is None or not callable(method):
        raise RuntimeError(
            f"{definition.name}: method '{definition.method_name}' not found "
            f"on {type(deps).__name__}"
        )
    return method


def _dispatcher(
    definition: ToolDefinition, schema: type[BaseModel]
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """The generic tool body: validate, call the bound method, normalize."""

    async def dispatch(ctx: RunContext[AgentToolsProtocol], **arguments: Any) -> Any:
        kwargs = _validated_kwargs(definition, schema, arguments)
        # Resolved outside the try: a missing method is a registry bug, not
        # something to hand the model as a tool error it could act on.
        method = _resolve_method(ctx.deps, definition)
        try:
            result = await method(**kwargs)
            # Normalization (e.g. band_read_room_file's image decode) can also
            # fail on malformed data, so it shares the method call's error
            # handling rather than crashing the run uncaught.
            return _normalized_result(definition, result)
        except Exception as error:
            logger.error(
                "%s failed (tool_call_id=%s): %s",
                definition.name,
                ctx.tool_call_id,
                error,
                exc_info=True,
            )
            # The "Error " prefix is load-bearing: band_tool_errored reads it to
            # tell a failed Band tool from productive work.
            message = f"Error executing {definition.name}: {error}"
            if definition.name == BandTool.RESPOND_CONTACT_REQUEST:
                await _send_contact_request_error_event(ctx.deps, message)
            return message

    return dispatch


def _normalized_result(definition: ToolDefinition, result: Any) -> Any:
    """A tool method's return value in the shape pydantic-ai should carry.

    ``band_read_room_file``'s image branch is the one result that is not data
    for the transcript but content for the model to look at.
    """
    if is_image_passthrough_result(definition.name, result):
        return [
            BinaryContent(data=data, media_type=media_type)
            for data, media_type in (
                decode_image_block(block) for block in result["content"]
            )
        ]
    return serialize_tool_result(result)


async def _send_contact_request_error_event(
    deps: AgentToolsProtocol, message: str
) -> None:
    """Put a failed ``band_respond_contact_request`` in the room, not just the transcript.

    A contact request the agent failed to answer looks, from the room, exactly
    like one it never received — so this failure is reported where a human can
    see it. Best effort: a turn must not fail over its own error reporting.
    """
    try:
        await deps.send_event(message, "error")
    except Exception as error:
        logger.warning("Failed to report the contact-request failure: %s", error)


__all__ = ["build_band_pydantic_ai_tools"]
