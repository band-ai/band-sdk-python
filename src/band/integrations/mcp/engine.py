"""The one MCP tool-registration engine.

Collapses band-mcp's FastMCP-based registrar and ``LocalMCPServer``'s
hand-rolled lowlevel-``Server`` registration into a single, FastMCP-based
engine consumed by two front-door factories:

- ``packages/band-mcp``'s ``standalone_spec(config)`` -- the published CLI.
- ``src/band/integrations/mcp/local_server.py``'s ``embedded_spec(...)`` --
  the in-process front door for opencode/letta/claude_sdk/acp.

Each factory normalizes its door's configuration into a tuple of
``MCPToolRegistration``s (room field already extended/pinned, event-width
override applied, custom tools included) and hands the engine an immutable
``EngineSpec``. ``build_engine`` is a pure function of that spec: it carries
zero door-conditionals -- every per-door difference is resolved by the
factory before the engine ever sees it.

MCP-version isolation: this module is one of the few allowlisted places
``mcp``-package types may appear.
``EngineSpec``, ``MCPToolRegistration``, ``CustomToolSpec``, and
``ToolsResolver`` are themselves framework-neutral -- no ``mcp``-package type
appears in their own fields -- so a v1->v2 migration only has to touch this
module's FastMCP-translation internals, not every caller.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ImageContent
from pydantic import AliasChoices, BaseModel, Field, create_model
from pydantic.fields import FieldInfo
from pydantic.json_schema import SkipJsonSchema

from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol
from band.core.types import Capability, EventMessageType, MessageType
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
)
from band.runtime.tools import (
    CHAT_ID_FIELD_NAME,
    CHAT_ID_MAX_LENGTH,
    READ_ROOM_FILE_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    SendEventInput,
    Surface,
    ToolDefinition,
    append_available_mention_handles,
    is_mcp_content_result,
    iter_tool_definitions,
    serialize_tool_result,
    validate_tool_arguments,
)

logger = logging.getLogger(__name__)

MCPToolExecutor = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class MCPToolRegistration:
    """A single tool, fully normalized by a front-door factory.

    ``input_model`` already carries whatever room-field extension or pin the
    owning factory decided on -- the engine never inspects tool identity to
    make that call, it just wires whatever the factory handed it.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    execute: MCPToolExecutor
    # Passed straight through to FastMCP's add_tool(structured_output=...).
    # None (default) preserves today's auto-inferred behavior for every
    # tool. False is for band_read_room_file only: its image branch returns
    # real MCP content blocks (see _mcp_content_blocks), and a declared
    # structured-output schema would validate that non-str return value
    # against the model FastMCP infers from the dispatch function's return
    # annotation and reject it.
    structured_output: bool | None = None


@dataclass(frozen=True)
class EngineSpec:
    """Framework-neutral input to :func:`build_engine`.

    ``tools`` is fully normalized: room field, pinning, event-width
    overrides, and custom tools are all already applied by the factory that
    built this spec.
    """

    name: str
    tools: tuple[MCPToolRegistration, ...]


@dataclass(frozen=True)
class CustomToolSpec:
    """Declarative custom-tool definition: an input model and its handler.

    Replaces the bare ``(input_model, handler)`` tuple (``CustomToolDef``)
    with a named, typed shape. The tuple form is still accepted wherever a
    ``CustomToolSpec | CustomToolDef`` is expected -- it's the existing
    adapter contract, not deprecated by this.
    """

    input_model: type[BaseModel]
    handler: Callable[..., Any]


class ToolsResolver(Protocol):
    """The one seam between a normalized registration and live tool state.

    Deliberately minimal and invocation-oriented: a single ``invoke()``.
    Everything resolver-specific -- locking, per-room caching, participant
    refresh, room-less sentinel handling -- lives inside a concrete
    resolver's own implementation, never in the engine or in
    :func:`build_tool_registration`.
    """

    async def invoke(
        self,
        definition: ToolDefinition,
        chat_id: str | None,
        arguments: dict[str, Any],
    ) -> Any: ...


class EmbeddedResolver:
    """SDK-owned resolver for the embedded front door.

    Calls adapter-owned tools directly through a room-lookup callback the
    adapter already maintains -- no cache, no lock: the adapter's per-room
    ``AgentTools`` instance is already live and WS-updated, so there is
    nothing here worth re-caching (divergence-matrix row 11).
    """

    def __init__(self, get_tools: Callable[[str], Any]) -> None:
        self._get_tools = get_tools

    async def invoke(
        self,
        definition: ToolDefinition,
        chat_id: str | None,
        arguments: dict[str, Any],
    ) -> Any:
        # Embedded's uniform wrap (row 2) makes chat_id required on every
        # agent tool's advertised schema, so validation already rejects a
        # missing one before dispatch reaches here -- this is a defensive
        # narrowing for the type checker and a clear error, not a real path.
        if chat_id is None:
            raise ValueError(f"{definition.name}: missing chat_id for room-bound tool")
        tools = self._get_tools(chat_id)
        if tools is None:
            raise ValueError(f"No tools available for room {chat_id}")
        return await dispatch_tool(tools, definition, arguments)


def resolve_tool_method(tools: Any, definition: ToolDefinition) -> Callable[..., Any]:
    """Look up ``definition.method_name`` on ``tools``, or raise an actionable error.

    Every :class:`ToolsResolver` dispatches this way; centralizing the lookup
    means a ``ToolDefinition.method_name`` registry mistake (a typo, a stale
    entry) surfaces as this message instead of a raw ``AttributeError`` at
    whichever call site hit it first.
    """
    method = getattr(tools, definition.method_name, None)
    if method is None or not callable(method):
        raise RuntimeError(
            f"{definition.name}: method '{definition.method_name}' not found "
            f"on {type(tools).__name__}"
        )
    return method


async def dispatch_tool(
    tools: Any,
    definition: ToolDefinition,
    arguments: dict[str, Any],
) -> Any:
    """Resolve and call ``definition``'s method on ``tools``.

    Shared by every :class:`ToolsResolver` implementation (embedded and
    standalone) so the method-not-found guard and the ``band_send_message``
    mention-hint enrichment below live in one place instead of being
    duplicated per resolver.
    """
    method = resolve_tool_method(tools, definition)
    try:
        return await method(**arguments)
    except (ValueError, BandToolError) as error:
        raise enrich_send_message_error(definition, tools, error) from error


def enrich_send_message_error(
    definition: ToolDefinition,
    tools: Any,
    error: ValueError | BandToolError,
) -> ValueError | BandToolError:
    """Append available mention handles to a failed ``band_send_message`` call.

    Benefits both the published CLI and embedded consumers. Any other
    tool's error passes through unchanged. ``tools`` needs only a
    ``.participants`` attribute and an
    optional ``.agent_id`` -- resolver-agnostic on purpose, so both
    ``EmbeddedResolver`` above and the CLI's ``StandaloneResolver`` can call
    this with whatever tools instance they hold.
    """
    if definition.name != SEND_MESSAGE_TOOL_NAME:
        return error
    message = append_available_mention_handles(
        str(error),
        getattr(tools, "participants", []),
        getattr(tools, "agent_id", None),
    )
    return type(error)(message)


def _is_skip_json_schema(field_info: FieldInfo) -> bool:
    """True if ``field_info``'s annotation is ``SkipJsonSchema[...]``."""
    metadata = getattr(field_info, "metadata", None) or []
    for meta in metadata:
        if meta.__class__.__name__ == "SkipJsonSchema":
            return True
    return "SkipJsonSchema" in repr(field_info.annotation)


def extend_with_chat_id(
    original: type[BaseModel],
    pinned_room_id: str | None,
) -> type[BaseModel]:
    """Return a subclass of ``original`` that ADDS a ``chat_id`` field.

    For agent room-bound tools: ``AgentTools`` is constructor-scoped, so its
    SDK input models carry no room field at all -- this is the layer that
    adds one. A caller that already has a native ``chat_id`` field on a
    room-bound model (the human surface) wants :func:`pin_existing_chat_id`
    instead, not this.

    - Unpinned (``pinned_room_id=None``): ``chat_id`` is a required ``str``
      with ``validation_alias=AliasChoices("chat_id", "room_id")`` so callers
      can post either name.
    - Pinned: ``chat_id`` is ``SkipJsonSchema[str | None]`` defaulted to
      ``None`` -- hidden from the advertised schema but still accepted by
      the validator if a client sends it. The caller injects
      ``pinned_room_id`` into the dispatched arguments before validation.
    """
    if pinned_room_id is None:
        model = create_model(  # type: ignore[call-overload]
            f"{original.__name__}WithChatId",
            __base__=original,
            **{
                CHAT_ID_FIELD_NAME: (
                    str,
                    Field(
                        ...,
                        max_length=CHAT_ID_MAX_LENGTH,
                        validation_alias=AliasChoices(CHAT_ID_FIELD_NAME, "room_id"),
                        # Model-facing text says only "chat_id" -- the alias
                        # above still accepts a legacy "room_id" caller, but
                        # that alternate name must never appear in text the
                        # model sees.
                        description="ID of the chat room.",
                    ),
                )
            },
        )
        model.__doc__ = original.__doc__
        return model
    return pin_existing_chat_id(original)


def pin_existing_chat_id(original: type[BaseModel]) -> type[BaseModel]:
    """Return a subclass that re-annotates an existing ``chat_id`` as pinned.

    For human room-bound tools, whose input models already carry a plain
    ``chat_id`` field (``HumanTools`` is not constructor-scoped, so it was
    never missing one the way agent tools are). The advertised schema omits
    the field; an inbound value is still accepted via alias so a client that
    sends ``chat_id`` explicitly doesn't fail validation. The actual pinned
    value is injected into the dispatched arguments before validation by
    ``build_tool_registration``'s own ``pinned_room_id`` parameter, not by
    this function -- it only reshapes the schema.
    """
    model = create_model(  # type: ignore[call-overload]
        f"{original.__name__}Pinned",
        __base__=original,
        **{
            CHAT_ID_FIELD_NAME: (
                SkipJsonSchema[str | None],
                Field(
                    default=None,
                    max_length=CHAT_ID_MAX_LENGTH,
                    validation_alias=AliasChoices(CHAT_ID_FIELD_NAME, "room_id"),
                    description="Pinned room id (hidden from advertised schema).",
                ),
            )
        },
    )
    model.__doc__ = original.__doc__
    return model


# The CLI door's own widening of EventMessageType (divergence-matrix row 6):
# a standalone MCP agent has no adapter narrating tool_call/tool_result
# events on its behalf, so it needs a self-narration channel the embedded
# SDK doesn't. Derived from EventMessageType (not retyped) so the two stay
# single-sourced -- a future addition to the narrow set is picked up here
# automatically. Lives here, not in band.core.types, since this engine is
# its only consumer.
WideEventMessageType = Literal[
    EventMessageType, MessageType.TOOL_CALL, MessageType.TOOL_RESULT
]

# Not a subclass of SendEventInput: widening a field's type in a subclass is
# unsound for a mutable (assignable) Pydantic field -- a caller holding a
# SendEventInput reference could otherwise observe a message_type value
# outside its own narrower literal. Same fields, independent model.
#
# __doc__ is reused verbatim from SendEventInput below (not restated here):
# it is the published band-mcp wire description, unaffected by the wider
# enum -- the old registrar's widened model made the identical choice
# (`model.__doc__ = original.__doc__`), and the wire-schema snapshot test
# pins this exactly.
#
# content/metadata are unchanged from the master -- reused directly from its
# own FieldInfo (create_model copies rather than shares it, so this creates
# no link back to SendEventInput) instead of retyping their descriptions,
# which would silently drift from the master on an edit there. Only
# message_type is a genuine override, for the widened enum.
SendEventWideInput = create_model(  # type: ignore[call-overload]
    "SendEventWideInput",
    content=(str, SendEventInput.model_fields["content"]),
    message_type=(
        WideEventMessageType,
        Field(
            ...,
            description="Type of event: tool_call, tool_result, thought, error, or task.",
        ),
    ),
    metadata=(dict[str, Any] | None, SendEventInput.model_fields["metadata"]),
)
SendEventWideInput.__doc__ = SendEventInput.__doc__


def _build_handler_signature(input_model: type[BaseModel]) -> inspect.Signature:
    """Build the ``inspect.Signature`` FastMCP derives the advertised schema from.

    One keyword-only parameter per visible field of ``input_model``. Fields
    annotated ``SkipJsonSchema[...]`` are omitted -- those are pinned-mode
    fields injected server-side, which MUST NOT appear in the advertised
    schema. ``validation_alias`` (e.g. the chat_id/room_id alias) is copied
    onto the synthesized parameter so FastMCP's own generated arg model
    accepts the alternate name too. ``field_info.metadata`` (the
    ``annotated_types`` constraint markers a ``Field(ge=..., le=...,
    max_length=..., pattern=...)`` call attaches) is carried forward via
    ``rebuild_annotation()`` -- Pydantic's own reconstruction of
    ``Annotated[type, *metadata]`` -- so FastMCP's schema keeps advertising
    the same bounds ``input_model.model_json_schema()`` would. Without it,
    every numeric/length/pattern constraint would silently disappear from the
    wire schema even though ``validate_tool_arguments`` still enforces it at
    call time against the original ``input_model``.
    """
    parameters: list[inspect.Parameter] = []
    for field_name, field_info in input_model.model_fields.items():
        if _is_skip_json_schema(field_info):
            continue
        base_annotation = field_info.rebuild_annotation()
        if base_annotation is None:
            base_annotation = Any

        field_kwargs: dict[str, Any] = {}
        if field_info.validation_alias is not None:
            field_kwargs["validation_alias"] = field_info.validation_alias
        if field_info.description:
            field_kwargs["description"] = field_info.description

        annotation = (
            Annotated[base_annotation, Field(**field_kwargs)]
            if field_kwargs
            else base_annotation
        )

        if field_info.is_required():
            default = inspect.Parameter.empty
        elif field_info.default_factory is not None:
            # ``field_info.default`` is Pydantic's ``PydanticUndefined``
            # sentinel for a factory-only field -- passing that through as a
            # literal default makes create_model() below read it as "no
            # default provided" and mark the field required, the opposite of
            # what a default_factory field should advertise. A real
            # ``Field(default_factory=...)`` here reproduces
            # ``model_json_schema()``'s own behavior instead: optional, no
            # advertised default value.
            default = Field(default_factory=field_info.default_factory)
        else:
            default = field_info.default
        parameters.append(
            inspect.Parameter(
                field_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                annotation=annotation,
                default=default,
            )
        )

    return inspect.Signature(parameters=parameters, return_annotation=str)


def _make_dispatch_function(
    registration: MCPToolRegistration,
) -> Callable[..., Awaitable[str]]:
    """Synthesize the function FastMCP's ``add_tool`` derives a schema from.

    FastMCP inspects a real function's signature (via ``Tool.from_function``)
    to build the advertised JSON schema -- there is no API to hand it an
    explicit schema dict directly. This is why the schema-shaping work above
    happens on ``registration.input_model`` (a real Pydantic model) rather
    than on a hand-built schema dict.
    """
    signature = _build_handler_signature(registration.input_model)

    async def _dispatch(**kwargs: Any) -> str:
        return await registration.execute(kwargs)

    _dispatch.__signature__ = signature  # type: ignore[attr-defined]
    _dispatch.__name__ = registration.name
    _dispatch.__doc__ = registration.description or f"Execute {registration.name}"
    annotations: dict[str, Any] = {
        parameter.name: parameter.annotation
        for parameter in signature.parameters.values()
    }
    annotations["return"] = str
    _dispatch.__annotations__ = annotations
    return _dispatch


def build_tool_registration(
    definition: ToolDefinition,
    input_model: type[BaseModel],
    *,
    resolver: ToolsResolver,
    strip_chat_id: bool,
    pinned_room_id: str | None = None,
) -> MCPToolRegistration:
    """Build one registration for a built-in (agent/human) tool definition.

    Shared by both front-door factories -- only the arguments differ per
    door/tool, never the dispatch logic itself:

    - ``input_model``: already room-extended/pinned by the caller (see
      :func:`extend_with_chat_id` / :func:`pin_existing_chat_id`), or
      ``definition.input_model`` unchanged for a room-less tool.
    - ``strip_chat_id``: pop ``chat_id`` before calling the resolver (agent
      tools -- ``AgentTools`` is constructor-scoped, its methods don't take
      one) vs. leave it in the dispatched arguments (human tools -- a normal
      method parameter there).
    - ``pinned_room_id``: inject-and-override ``chat_id`` before validation
      when set (CLI-only feature; the embedded door never pins).
    """

    is_read_room_file = definition.name == READ_ROOM_FILE_TOOL_NAME

    async def execute(arguments: dict[str, Any]) -> Any:
        kwargs = dict(arguments)
        if pinned_room_id is not None:
            kwargs[CHAT_ID_FIELD_NAME] = pinned_room_id
        validated = validate_tool_arguments(definition.name, input_model, kwargs)
        chat_id = (
            validated.pop(CHAT_ID_FIELD_NAME, None)
            if strip_chat_id
            else validated.get(CHAT_ID_FIELD_NAME)
        )
        result = await resolver.invoke(definition, chat_id, validated)
        if is_read_room_file and is_mcp_content_result(result):
            return _mcp_content_blocks(result)
        return _serialize(result)

    return MCPToolRegistration(
        name=definition.name,
        description=(input_model.__doc__ or "").strip(),
        input_model=input_model,
        execute=execute,
        structured_output=False if is_read_room_file else None,
    )


def build_custom_tool_registration(
    spec: CustomToolSpec | CustomToolDef,
    *,
    room_bound: bool = False,
) -> MCPToolRegistration:
    """Build a registration for a user-provided custom tool.

    Embedded-door only (divergence-matrix row 12: not exposed on the CLI).
    Dispatches straight through ``execute_custom_tool`` -- there is no
    ``AgentTools``/``HumanTools`` method behind a custom tool, so no
    resolver is involved.
    """
    tool_def: CustomToolDef = (
        (spec.input_model, spec.handler) if isinstance(spec, CustomToolSpec) else spec
    )
    input_model, _ = tool_def
    tool_name = get_custom_tool_name(input_model)
    model = extend_with_chat_id(input_model, None) if room_bound else input_model

    async def execute(arguments: dict[str, Any]) -> Any:
        kwargs = dict(arguments)
        kwargs.pop(CHAT_ID_FIELD_NAME, None)
        result = await execute_custom_tool(tool_def, kwargs)
        return _serialize(result)

    return MCPToolRegistration(
        name=tool_name,
        description=(input_model.__doc__ or "").strip(),
        input_model=model,
        execute=execute,
    )


RoomToolResolver = Callable[[str], AgentToolsProtocol | None]


def _filter_to_agent_surface(
    definitions: Sequence[ToolDefinition],
) -> list[ToolDefinition]:
    """Drop non-agent definitions and log a warning for each discarded entry.

    ``build_*_tool_registrations`` wire their execution path through
    ``AgentTools``; a ``surface="human"`` definition in the list would
    ``AttributeError`` at call time because ``AgentTools`` has no
    ``HumanTools`` methods. Rather than propagate the error, quietly filter
    and warn so a regression in a caller is observable but not fatal.
    """
    filtered: list[ToolDefinition] = []
    for definition in definitions:
        if definition.surface != Surface.AGENT:
            logger.warning(
                "Dropping non-agent tool definition %r (surface=%r) from MCP "
                "registrations; the embedded door is agent-only.",
                definition.name,
                definition.surface,
            )
            continue
        filtered.append(definition)
    return filtered


def _resolve_agent_definitions(
    *,
    capabilities: frozenset[Capability] | None,
    tool_definitions: Sequence[ToolDefinition] | None,
) -> list[ToolDefinition]:
    if tool_definitions is not None:
        return _filter_to_agent_surface(list(tool_definitions))
    return list(iter_tool_definitions(surface=Surface.AGENT, capabilities=capabilities))


def build_band_mcp_tool_registrations(
    agent_tools: AgentToolsProtocol,
    *,
    capabilities: frozenset[Capability] | None = None,
    additional_tools: list[CustomToolDef] | None = None,
    tool_definitions: Sequence[ToolDefinition] | None = None,
) -> list[MCPToolRegistration]:
    """Build MCP tool registrations bound to a single, already-live ``AgentTools``.

    For a caller with exactly one room per server instance (e.g. an ACP
    session) -- no room resolution needed, so every ``chat_id`` resolves to
    the same ``agent_tools`` regardless of its value.
    """
    return build_resolved_band_mcp_tool_registrations(
        get_tools=lambda _chat_id: agent_tools,
        capabilities=capabilities,
        additional_tools=additional_tools,
        tool_definitions=tool_definitions,
    )


def build_resolved_band_mcp_tool_registrations(
    *,
    get_tools: RoomToolResolver,
    capabilities: frozenset[Capability] | None = None,
    additional_tools: list[CustomToolDef] | None = None,
    tool_definitions: Sequence[ToolDefinition] | None = None,
) -> list[MCPToolRegistration]:
    """Build MCP registrations that resolve room-scoped tools at call time.

    Uniform room-wrap: every agent tool gets a ``chat_id`` field here,
    regardless of the CLI door's ``AGENT_ROOM_BOUND_TOOL_NAMES``
    classification -- ``chat_id`` is this door's routing key for
    ``AgentTools`` instance selection (e.g. opencode's ``_get_room_tools``),
    so even a CLI-room-less tool like ``band_create_chatroom`` needs one here.
    """
    definitions = _resolve_agent_definitions(
        capabilities=capabilities, tool_definitions=tool_definitions
    )
    resolver = EmbeddedResolver(get_tools=get_tools)
    registrations = [
        build_tool_registration(
            definition,
            extend_with_chat_id(definition.input_model, None),
            resolver=resolver,
            strip_chat_id=True,
        )
        for definition in definitions
    ]
    registrations.extend(
        build_custom_tool_registration(tool_def, room_bound=True)
        for tool_def in additional_tools or []
    )
    validate_unique_tool_names(registrations)
    return registrations


def _mcp_content_blocks(result: dict[str, Any]) -> list[ImageContent]:
    """Convert an MCP-content-shaped tool result into real MCP content blocks.

    ``is_mcp_content_result`` already confirmed ``result["content"]`` is a
    list of MCP content block dicts. FastMCP's own result conversion
    recognizes an actual ``ContentBlock`` instance (or a list of them) and
    passes it through to the client verbatim -- a plain dict does not match
    and would otherwise fall through to JSON-text encoding, same as any
    other tool result. Only "image" blocks exist today (the sole structured
    branch of ``AgentTools.read_room_file``); a block of any other shape
    fails Pydantic validation here rather than silently being dropped.
    """
    return [ImageContent(**block) for block in result["content"]]


def _serialize(result: Any) -> str:
    """Serialize a tool method's return value to a JSON string for the wire.

    The published band-mcp CLI shape (divergence-matrix row 15) -- now
    universal for both doors: raw-string passthrough, ``serialize_tool_result``
    (the single source of truth for model_dump-ing a Pydantic tool result --
    see its docstring) otherwise. Embedded callers' LLMs see this shape too
    now (previously a ``{"result": x}`` dict-wrap); flagged as an intentional
    change in the PR, verified by the e2e backends lane. No ``indent``: this
    payload has no human reader, only pretty-printing token cost.
    """
    if result is None:
        return json.dumps(None)
    if isinstance(result, str):
        return result
    return json.dumps(serialize_tool_result(result), default=str)


def validate_unique_tool_names(registrations: Sequence[MCPToolRegistration]) -> None:
    """Raise if any two registrations share a name (divergence-matrix row 8).

    One check, covering every surface and custom tools together -- band-mcp
    and ``LocalMCPServer`` each had their own version of this; this is the
    single engine-level replacement.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for registration in registrations:
        if registration.name in seen:
            duplicates.add(registration.name)
            continue
        seen.add(registration.name)
    if duplicates:
        raise ValueError(f"Duplicate MCP tool names: {', '.join(sorted(duplicates))}")


def build_engine(
    spec: EngineSpec,
    *,
    host: str = "127.0.0.1",
    transport_security: TransportSecuritySettings | None = None,
    sse_path: str = "/sse",
    message_path: str = "/messages/",
    streamable_http_path: str = "/mcp",
) -> FastMCP:
    """Build a fresh ``FastMCP`` instance from a normalized ``EngineSpec``.

    A pure function of ``spec``: no door-conditionals live here, only the
    registration -> FastMCP translation shared by every consumer. Always
    returns a brand-new ``FastMCP`` -- the embedded door's session managers
    are single-use, so a caller doing a start/stop/start lifecycle must call
    this again per start rather than reuse the returned instance.

    The path overrides default to FastMCP's own defaults; they exist so
    ``local_server.py`` can preserve ``LocalMCPServer``'s existing
    constructor surface (published band-sdk API) unchanged.

    ``host`` must be the caller's *real* bind address, even though this
    engine never binds a socket itself (every caller mounts its ASGI app on
    a socket/uvicorn config of its own). FastMCP's own constructor
    auto-enables loopback-only DNS-rebinding protection when
    ``transport_security is None and host in ("127.0.0.1", "localhost",
    "::1")`` -- if a caller bound to a non-loopback host (e.g.
    ``LocalMCPServer``'s documented ``0.0.0.0`` support for a Docker
    callback) never told FastMCP that, FastMCP would still see its own
    ``host="127.0.0.1"`` default and wrongly lock the allowlist to loopback,
    rejecting every real non-loopback caller with a 421.
    """
    validate_unique_tool_names(spec.tools)
    mcp = FastMCP(
        name=spec.name,
        host=host,
        transport_security=transport_security,
        sse_path=sse_path,
        message_path=message_path,
        streamable_http_path=streamable_http_path,
    )
    for registration in spec.tools:
        handler = _make_dispatch_function(registration)
        mcp.add_tool(
            handler,
            name=registration.name,
            description=registration.description,
            structured_output=registration.structured_output,
        )
    return mcp
