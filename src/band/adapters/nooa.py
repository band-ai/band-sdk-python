"""Band adapter for NOOA (NVIDIA-labs Object Oriented Agents).

A NOOA agent is a Python class whose ``...``-bodied async methods are completed
by an LLM that writes and runs Python against ``self``. Band tools therefore
reach the model as async methods on a live ``self.room`` object, not as JSON
tool schemas: the model reads their signatures and docstrings and calls them
from its generated code.
"""

from __future__ import annotations

import inspect
import json
import keyword
import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ClassVar, cast
from uuid import uuid4

from band_sdk_core import AgentFailure
from pydantic import BaseModel
from pydantic.fields import FieldInfo

try:
    from nooa import Agent, Context, Image, hidden
    from nooa.agentdoc import doc
    from nooa.context_blocks import EventBase
    from nooa.events import LLMComplete
except ModuleNotFoundError as error:
    # ModuleNotFoundError keeps the missing module's name, so a venv without the
    # extra can still tell "absent optional dependency" from a broken import.
    raise ModuleNotFoundError(
        "NOOA dependencies not installed (NOOA supports Python 3.12 and 3.13 on "
        "Linux and macOS). Install with: uv add band-sdk[nooa]",
        name=error.name,
    ) from error

from typing_extensions import Unpack

from band.converters.nooa import NooaHistoryConverter, NooaTranscript
from band.core.adapterconfig import BaseAdapterConfig
from band.core.exceptions import BandToolError
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    AgentToolsProtocol,
)
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    MessageType,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
)
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
    reject_conflicting_tool_names,
)
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    ToolDefinition,
    decode_image_block,
    get_band_tool_category,
    image_block_placeholder,
    is_image_passthrough_result,
    iter_tool_definitions,
    redact_tool_call_args,
    serialize_tool_result,
    validate_tool_arguments,
)

logger = logging.getLogger(__name__)

_PROVIDER = "nooa"

INSTRUCTIONS_BLOCK = "band_instructions"
TOOLS_BLOCK = "band_tools"
TRANSCRIPT_BLOCK = "band_room_transcript"

ROOM_TOOLS_SECTION = """\
## Band tools in Python
Every Band tool named in these instructions is an async method on `self.room`,
called from `execute_python` with keyword arguments, for example
`await self.room.band_send_message(content="...", mentions=["..."])`.
A failed call raises `BandToolError` with the reason. An image file read with
`band_read_room_file` comes back as a list of `Image` objects; `show(image)`
lets you see one. After you reply or decline, finish with `return_result(None)`."""


ToolRunner = Callable[[AgentToolsProtocol, dict[str, Any]], Awaitable[object]]


@dataclass(frozen=True)
class RoomToolSpec:
    """One tool the model can call as ``await self.room.<name>(...)``."""

    name: str
    input_model: type[BaseModel]
    run: ToolRunner


class RoomTools:
    """Band tools for the current room. Every method is async: `await self.room.<method>(...)`."""

    _specs: ClassVar[Mapping[str, RoomToolSpec]] = {}

    def __init__(self, *, emit_tool_calls: bool) -> None:
        self._emit_tool_calls = emit_tool_calls
        self._tools: AgentToolsProtocol | None = None

    def __repr__(self) -> str:
        return f"RoomTools({', '.join(self._specs)})"

    @hidden
    @contextmanager
    def bound_to(self, tools: AgentToolsProtocol) -> Iterator[None]:
        """Route calls to one turn's tools for the duration of that turn."""
        self._tools = tools
        try:
            yield
        finally:
            self._tools = None

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        tools = self._tools
        if tools is None:
            raise BandToolError(
                f"{name} is only available while a Band room message is being handled"
            )
        spec = self._specs[name]
        call_id = uuid4().hex
        await self._report(
            tools,
            MessageType.TOOL_CALL,
            {
                ToolEventKey.NAME: name,
                ToolEventKey.ARGS: redact_tool_call_args(name, arguments),
                ToolEventKey.TOOL_CALL_ID: call_id,
            },
        )
        try:
            value = serialize_tool_result(await spec.run(tools, arguments))
        except Exception as error:
            message = f"{name} failed: {error}"
            logger.warning("Band tool %s failed: %s", name, error)
            await self._report_result(tools, name, call_id, message, is_error=True)
            raise BandToolError(message) from error
        if is_image_passthrough_result(name, value):
            images = images_from_result(cast(dict[str, Any], value))
            output = image_block_placeholder(len(images))
            await self._report_result(tools, name, call_id, output, is_error=False)
            return images
        await self._report_result(tools, name, call_id, value, is_error=False)
        return value

    async def _report_result(
        self,
        tools: AgentToolsProtocol,
        name: str,
        call_id: str,
        output: object,
        *,
        is_error: bool,
    ) -> None:
        await self._report(
            tools,
            MessageType.TOOL_RESULT,
            {
                ToolEventKey.NAME: name,
                ToolEventKey.OUTPUT: output,
                ToolEventKey.TOOL_CALL_ID: call_id,
                ToolEventKey.IS_ERROR: is_error,
            },
        )

    async def _report(
        self,
        tools: AgentToolsProtocol,
        message_type: MessageType,
        payload: Mapping[ToolEventKey, object],
    ) -> None:
        if not self._emit_tool_calls:
            return
        try:
            await tools.send_event(
                content=json.dumps(payload, default=str),
                message_type=message_type,
            )
        except Exception as error:  # noqa: BLE001 -- event emission is best-effort and must not fail the tool call
            logger.warning("Failed to send %s event: %s", message_type, error)


def images_from_result(result: dict[str, Any]) -> list[Image]:
    """NOOA images for an image-file read, so the model can ``show()`` them."""
    images: list[Image] = []
    for block in result["content"]:
        data, mime_type = decode_image_block(block)
        images.append(cast(Image, Image.from_bytes(data, media_type=mime_type)))
    return images


def tool_method_doc(input_model: type[BaseModel]) -> str:
    """The docstring the model reads: the tool description plus its arguments."""
    description = inspect.cleandoc(input_model.__doc__ or "")
    arguments = [
        f"    {name}: {field.description}" if field.description else f"    {name}"
        for name, field in input_model.model_fields.items()
    ]
    if not arguments:
        return description
    return "\n".join([description, "", "Args:", *arguments])


class ComputedDefault:
    """Stands in for a ``default_factory`` default in a rendered signature.

    Calling the factory just to document it could fail (a factory may take the
    validated data) or build an expensive object.
    """

    def __repr__(self) -> str:
        return "<computed>"


def documented_default(field: FieldInfo) -> object:
    """The default a signature shows for *field*."""
    if field.is_required():
        return inspect.Parameter.empty
    if field.default_factory is not None:
        return ComputedDefault()
    return field.default


def tool_method_signature(input_model: type[BaseModel]) -> inspect.Signature:
    """Keyword-only parameters mirroring the tool's input model."""
    parameters = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    for name, field in input_model.model_fields.items():
        default = documented_default(field)
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=field.annotation,
                default=default,
            )
        )
    return inspect.Signature(parameters, return_annotation=Any)


def tool_method(spec: RoomToolSpec) -> Callable[..., Awaitable[Any]]:
    """An async method that forwards its keyword arguments to the tool."""

    async def method(self: RoomTools, **arguments: Any) -> Any:
        return await self._invoke(spec.name, arguments)

    method.__name__ = spec.name
    method.__qualname__ = f"{RoomTools.__name__}.{spec.name}"
    method.__doc__ = tool_method_doc(spec.input_model)
    method.__signature__ = tool_method_signature(spec.input_model)  # type: ignore[attr-defined]
    return method


def room_tools_class(specs: list[RoomToolSpec]) -> type[RoomTools]:
    """A ``RoomTools`` subclass exposing exactly *specs* as methods."""
    namespace: dict[str, Any] = {spec.name: tool_method(spec) for spec in specs}
    namespace["_specs"] = {spec.name: spec for spec in specs}
    namespace["__doc__"] = RoomTools.__doc__
    namespace["__module__"] = RoomTools.__module__
    namespace["__qualname__"] = RoomTools.__qualname__
    return type(RoomTools.__name__, (RoomTools,), namespace)


def room_tools_reference(tools_class: type[RoomTools]) -> str:
    """Every tool's full signature and docstring, as the model reads them.

    NOOA's view of ``self`` shows only the first docstring line of a referenced
    type's methods, which would drop each tool's argument descriptions.
    """
    return doc(tools_class)


def platform_tool_spec(definition: ToolDefinition) -> RoomToolSpec:
    """Dispatch to the typed ``AgentToolsProtocol`` method for a Band tool."""

    async def run(tools: AgentToolsProtocol, arguments: dict[str, Any]) -> object:
        validated = validate_tool_arguments(
            definition.name, definition.input_model, arguments
        )
        method = cast(
            Callable[..., Awaitable[object]], getattr(tools, definition.method_name)
        )
        return await method(**validated)

    return RoomToolSpec(definition.name, definition.input_model, run)


def custom_tool_spec(tool_def: CustomToolDef) -> RoomToolSpec:
    """Dispatch to a portable ``(InputModel, handler)`` custom tool."""
    input_model, _ = tool_def

    async def run(tools: AgentToolsProtocol, arguments: dict[str, Any]) -> object:
        return await execute_custom_tool(tool_def, arguments, turn=tools.turn)

    return RoomToolSpec(get_custom_tool_name(input_model), input_model, run)


def validate_custom_tool_names(custom_tools: list[CustomToolDef]) -> None:
    """Custom tools become ``RoomTools`` methods, so each name must be a public
    identifier that does not replace one of its own attributes."""
    names = [get_custom_tool_name(input_model) for input_model, _ in custom_tools]
    reject_conflicting_tool_names(names)
    if invalid := sorted(
        name
        for name in names
        if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_")
    ):
        raise ValueError(
            f"Custom tool names must be public Python identifiers: {invalid}"
        )
    if reserved := sorted(set(names) & set(dir(RoomTools))):
        raise ValueError(f"Custom tool names are reserved by RoomTools: {reserved}")


class NooaRoomAgent(Agent):
    """You are a participant in a Band chat room.

    Handle each incoming room message with the Band tools on `self.room`.
    """

    room: RoomTools

    async def respond(self, message: str) -> None:
        """Handle one incoming Band room message.

        Reply with `await self.room.band_send_message(...)`, or decline with
        `await self.room.band_no_reply(...)` when no reply is needed. Call other
        tools on `self.room` first when the reply needs them.
        """
        ...  # noqa: PIE790 -- NOOA hands an ellipsis-bodied method to the model


def with_room_tools(
    agent_class: type[NooaRoomAgent], tools_class: type[RoomTools]
) -> type[NooaRoomAgent]:
    """Subclass *agent_class* so ``room`` is annotated with *tools_class*.

    NOOA documents an agent to its model from the class annotations, so the
    model only sees the tools that the annotated type defines.
    """
    namespace = {
        "__annotations__": {"room": tools_class},
        "__doc__": agent_class.__doc__,
        "__module__": agent_class.__module__,
        "__qualname__": agent_class.__qualname__,
    }
    return cast(
        type[NooaRoomAgent],
        type(agent_class)(agent_class.__name__, (agent_class,), namespace),
    )


@contextmanager
def recording_usage(agent: NooaRoomAgent) -> Iterator[list[TurnUsage]]:
    """Collect the usage of every model call *agent* makes inside the block."""
    usages: list[TurnUsage] = []

    def record(event: EventBase) -> None:
        if not isinstance(event, LLMComplete):
            return
        usages.append(
            TurnUsage(
                input_tokens=event.prompt_tokens,
                output_tokens=event.completion_tokens,
                cache_read_tokens=event.cached_tokens,
            )
        )

    unsubscribe = agent.event_manager.on(LLMComplete.__name__, record)
    try:
        yield usages
    finally:
        unsubscribe()


async def report_failure(tools: AgentToolsProtocol) -> None:
    """Report a failed turn without letting the report replace its cause."""
    try:
        await tools.send_failure(
            AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
        )
    except Exception:
        logger.exception("Failed to report the NOOA turn failure")


def with_system_context(
    message: str, participants_msg: str | None, contacts_msg: str | None
) -> str:
    """Lead the message with any platform context that changed since last turn."""
    notices = [
        f"[System]: {notice}" for notice in (participants_msg, contacts_msg) if notice
    ]
    return "\n\n".join([*notices, message])


class NooaAdapterConfig(BaseAdapterConfig):
    """Settings for a NOOA agent.

    Attributes:
        custom_section: Extra instructions appended to the rendered Band
            instructions. The agent class docstring stays its system prompt.
    """

    custom_section: str | None = None


class NooaAdapter(SimpleAdapter[NooaTranscript]):
    """Run a NOOA agent class in Band rooms, one agent instance per room."""

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.TOOL_CALLS, Emit.USAGE})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: NooaAdapterConfig | None = None,
        *,
        agent: type[NooaRoomAgent],
        history_converter: NooaHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        """Create an adapter around a NOOA agent class.

        Args:
            config: Prompt settings; see :class:`NooaAdapterConfig`.
            agent: A ``NooaRoomAgent`` subclass, constructible without
                arguments. Each room gets its own instance, so instance state
                and conversation memory never cross rooms.
            history_converter: Optional custom history converter.
            additional_tools: Portable ``(InputModel, handler)`` custom tools,
                exposed on ``self.room`` beside the Band tools.
        """
        if not (isinstance(agent, type) and issubclass(agent, NooaRoomAgent)):
            raise TypeError("agent must be a NooaRoomAgent subclass")
        super().__init__(
            history_converter=history_converter or NooaHistoryConverter(),
            **features,
        )
        self.config = config or NooaAdapterConfig()
        self._custom_tools = additional_tools or []
        validate_custom_tool_names(self._custom_tools)
        self._agent_class = agent
        self._room_agent_class: type[NooaRoomAgent] | None = None
        self._room_tools_class: type[RoomTools] = RoomTools
        self._instructions = ""
        self._agents: dict[str, NooaRoomAgent] = {}

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Build the room tool surface once the effective features are known."""
        await super().on_started(agent_name, agent_description)
        self._instructions = render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section or "",
            features=self.features,
            extra_sections=(ROOM_TOOLS_SECTION,),
        )
        self._room_tools_class = room_tools_class(self._tool_specs())
        self._room_agent_class = with_room_tools(
            self._agent_class, self._room_tools_class
        )
        logger.info("NOOA adapter started for agent: %s", agent_name)

    def _tool_specs(self) -> list[RoomToolSpec]:
        """Band tools the features allow, then the custom tools.

        This controls what the model is told about and what ``self.room``
        offers. It is not an access boundary: generated code runs in-process
        and can reach the turn's tools object directly.
        """
        definitions = filter_tool_schemas(
            list(iter_tool_definitions(capabilities=self.features.capabilities)),
            self.features,
            get_name=lambda definition: definition.name,
            get_category=lambda definition: get_band_tool_category(definition.name),
        )
        return [platform_tool_spec(definition) for definition in definitions] + [
            custom_tool_spec(tool_def) for tool_def in self._custom_tools
        ]

    def _new_room_agent(self, transcript: NooaTranscript) -> NooaRoomAgent:
        if self._room_agent_class is None:
            raise RuntimeError("NooaAdapter.on_started must run before any message")
        agent = self._room_agent_class()
        agent.room = self._room_tools_class(
            emit_tool_calls=Emit.TOOL_CALLS in self.features.emit
        )
        agent.context[INSTRUCTIONS_BLOCK] = Context(
            value=self._instructions, prefix=True
        )
        agent.context[TOOLS_BLOCK] = Context(
            value=room_tools_reference(self._room_tools_class), prefix=True
        )
        if transcript:
            agent.context[TRANSCRIPT_BLOCK] = Context(
                value="\n".join(transcript), prefix=True
            )
        return agent

    def _room_agent(
        self, room_id: str, transcript: NooaTranscript, *, is_session_bootstrap: bool
    ) -> NooaRoomAgent:
        """The room's agent, recreated from platform history at session start."""
        if is_session_bootstrap or room_id not in self._agents:
            self._agents[room_id] = self._new_room_agent(transcript)
            if transcript:
                logger.debug(
                    "Room %s: seeded %s transcript line(s)", room_id, len(transcript)
                )
        return self._agents[room_id]

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: NooaTranscript,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run one room message through the agent's ``respond`` method."""
        agent = self._room_agent(
            room_id, history, is_session_bootstrap=is_session_bootstrap
        )
        message = with_system_context(
            msg.format_for_llm(), participants_msg, contacts_msg
        )
        with recording_usage(agent) as usages:
            try:
                with agent.room.bound_to(tools):
                    await agent.respond(message)
            except Exception:
                logger.exception("Room %s: NOOA turn failed", room_id)
                await report_failure(tools)
                raise
            finally:
                await self.emit_usage(tools, sum(usages, TurnUsage()))

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's agent when Band removes the adapter from a room."""
        if self._agents.pop(room_id, None) is not None:
            logger.debug("Room %s: Cleaned up NOOA agent", room_id)
