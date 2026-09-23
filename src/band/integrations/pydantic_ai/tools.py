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
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.messages import BinaryContent
from pydantic_core import SchemaValidator, core_schema

from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas, sanitize_tool_schema
from band.core.types import AdapterFeatures, MessageType
from band.runtime.tools import (
    BandTool,
    ToolDefinition,
    decode_image_block,
    get_band_tool_category,
    get_tool_description,
    is_image_passthrough_result,
    is_terminal_success,
    iter_tool_definitions,
    platform_args_schema,
    serialize_tool_result,
    validate_tool_arguments,
)

logger = logging.getLogger(__name__)

_BoundToolMethod = Callable[..., Coroutine[Any, Any, Any]]

_OBJECT_SCHEMA_VALIDATOR = SchemaValidator(core_schema.dict_schema())
"""Shared validator for the object-shape check every tool installs.

The schema is the same for every tool (just "is this an object"), and a
``SchemaValidator`` holds no per-tool state, so one instance is built once
and reused across every ``_build_tool`` call instead of once per tool."""


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
    strict_schema = _strict_schema(schema)
    tool = Tool.from_schema(
        _dispatcher(definition, strict_schema),
        # str(): ``ToolDefinition.name`` carries a ``BandTool`` member, and it
        # becomes a registry key pydantic-ai and its callers compare as a name.
        name=str(definition.name),
        description=get_tool_description(definition.name).strip(),
        # The plain schema, not the strict one: ``additionalProperties`` on an
        # advertised tool schema reaches some providers (e.g. Gemini, via
        # pydantic-ai's own ``GoogleJsonSchemaTransformer``, which doesn't
        # strip it) unsanitized and breaks tool calls there -- the same
        # keyword ``adapters/gemini.py`` already has to strip for that
        # adapter. Strictness only needs to affect validation, not the text
        # shown to the model.
        json_schema=sanitize_tool_schema(
            schema.model_json_schema(), drop_numeric_bounds=True
        ),
        takes_ctx=True,
        args_validator=_arguments_validator(definition, strict_schema),
    )
    # ``Tool.from_schema`` deliberately uses ``any_schema()`` because it skips
    # schema validation. Replace it with an object-shape validator so malformed
    # non-object payloads become ordinary validation retries.
    tool.function_schema.validator = _OBJECT_SCHEMA_VALIDATOR
    return tool


def _strict_schema(schema: type[BaseModel]) -> type[BaseModel]:
    """``schema``, rejecting arguments outside its accepted field names.

    ``Tool.from_schema`` does not enforce its JSON schema, and the master
    models intentionally ignore extras for non-tool callers, so without this
    an unrecognized argument would silently be dropped and the call would
    still dispatch. Rejecting via ``model_config`` -- rather than diffing the
    argument names against ``model_json_schema()``'s ``properties`` -- lets
    the one ``model_validate()`` call in ``validate_tool_arguments`` do the
    rejecting itself, so it correctly honors a field's ``validation_alias``
    secondary names the JSON schema never lists.

    Validation only (see ``_build_tool``) -- ``additionalProperties: false``
    on a schema *advertised* to the model reaches some providers unstripped
    (e.g. Gemini) and breaks tool calls there, so ``model_json_schema`` is
    disabled here rather than merely documented as off-limits: nothing else
    stops a future ``json_schema=`` call site from advertising this strict
    schema by mistake.
    """

    def _no_advertisement(*_args: Any, **_kwargs: Any) -> Any:
        raise TypeError(
            f"{schema.__name__}: this strict schema is for validation only "
            "-- advertise schema.model_json_schema() (the plain schema) to "
            "the model instead"
        )

    return type(
        schema.__name__,
        (schema,),
        {
            "model_config": ConfigDict(extra="forbid"),
            "__doc__": schema.__doc__,
            "model_json_schema": classmethod(_no_advertisement),
        },
    )


def _validated_kwargs(
    definition: ToolDefinition,
    schema: type[BaseModel],
    arguments: dict[str, Any],
    *,
    tool_call_id: str | None,
) -> dict[str, Any]:
    """``arguments`` as normalized kwargs, or a retry prompt for the model."""
    try:
        return validate_tool_arguments(definition.name, schema, arguments)
    except ValueError as error:
        logger.warning(
            "%s: argument validation failed (tool_call_id=%s): %s",
            definition.name,
            tool_call_id,
            error,
        )
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

    def validate(ctx: RunContext[AgentToolsProtocol], **arguments: Any) -> None:
        _validated_kwargs(definition, schema, arguments, tool_call_id=ctx.tool_call_id)

    return validate


def _resolve_method(
    deps: AgentToolsProtocol, definition: ToolDefinition
) -> _BoundToolMethod:
    """``definition.method_name`` bound on ``deps``, or an actionable error.

    A stale or typo'd registry entry should surface as this message, not a
    bare ``AttributeError`` from whichever tool call happens to hit it first.
    """
    method = getattr(deps, definition.method_name, None)
    if method is None or not callable(method):
        message = (
            f"{definition.name}: method '{definition.method_name}' not found "
            f"on {type(deps).__name__}"
        )
        logger.error("%s -- registry/protocol drift", message)
        raise RuntimeError(message)
    return cast(_BoundToolMethod, method)


def _dispatcher(
    definition: ToolDefinition, schema: type[BaseModel]
) -> _BoundToolMethod:
    """The generic tool body: validate, call the bound method, normalize."""

    async def dispatch(ctx: RunContext[AgentToolsProtocol], **arguments: Any) -> Any:
        kwargs = _validated_kwargs(
            definition, schema, arguments, tool_call_id=ctx.tool_call_id
        )
        # Resolved outside the try: a missing method is a registry bug, not
        # something to hand the model as a tool error it could act on.
        method = _resolve_method(ctx.deps, definition)
        try:
            result = await method(**kwargs)
        except Exception as error:  # noqa: BLE001 -- tool calls may raise any exception type; must surface to the LLM as an error string, not crash the turn
            return await _dispatch_failed(ctx, definition, error, notify_room=True)
        try:
            # Normalization (e.g. band_read_room_file's image decode) can also
            # fail on malformed data, so it gets the same graceful-error
            # treatment as a method-call failure rather than crashing the run
            # uncaught -- but it is kept in its own except, notify_room=False:
            # the method call already succeeded, so this is not a failure to
            # answer the request and must not be reported to the room as one.
            return _normalized_result(definition, result)
        except Exception as error:  # noqa: BLE001 -- normalization may fail on malformed data of any type; must surface to the LLM as an error string, not crash the turn
            return await _dispatch_failed(ctx, definition, error, notify_room=False)

    return dispatch


async def _dispatch_failed(
    ctx: RunContext[AgentToolsProtocol],
    definition: ToolDefinition,
    error: Exception,
    *,
    notify_room: bool,
) -> str:
    """Log and format a dispatch failure; optionally also report it to the room.

    Shared by the method-call and normalization failure paths so each keeps
    one place to log and format from, while only a genuine method-call
    failure (``notify_room=True``) can trigger the contact-request room event.
    """
    logger.error(
        "%s failed (tool_call_id=%s): %s",
        definition.name,
        ctx.tool_call_id,
        error,
        exc_info=error,
    )
    if notify_room or not is_terminal_success(definition.name, succeeded=True):
        # The "Error " prefix is load-bearing: band_tool_errored reads it to
        # tell a failed Band tool from productive work.
        message = f"Error executing {definition.name}: {error}"
    else:
        # The method completed, so this must not look like a failed terminal
        # action to the adapter even though the result could not be normalized.
        message = (
            f"{definition.name} executed, but its result could not be normalized: "
            f"{error}"
        )
    if notify_room and definition.name == BandTool.RESPOND_CONTACT_REQUEST:
        await _send_contact_request_error_event(ctx.deps, message)
    return message


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
        await deps.send_event(message, MessageType.ERROR)
    except Exception as error:  # noqa: BLE001 -- best-effort error-reporting fallback; must not raise if sending the error event itself fails
        logger.warning("Failed to report the contact-request failure: %s", error)


__all__ = ["build_band_pydantic_ai_tools"]
