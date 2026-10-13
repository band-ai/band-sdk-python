"""``NooaAdapter``: one NOOA agent instance per Band room.

A NOOA agent is a Python class whose ``...``-bodied async methods are completed
by an LLM that writes and runs Python against ``self``. Band tools reach the
model as async methods on a live ``self.room`` object (see ``tools``), not as
JSON tool schemas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar, cast

from band_sdk_core import AgentFailure

try:
    from nooa import Agent, Context
    from nooa.context_blocks import EventBase
    from nooa.events import LLMComplete
    from nooa.runtime.hooks import InstrumentationHooks, get_hooks, set_hooks
except ModuleNotFoundError as error:
    # ModuleNotFoundError keeps the missing module's name, so a venv without the
    # extra can still tell "absent optional dependency" from a broken import.
    raise ModuleNotFoundError(
        "NOOA dependencies not installed (NOOA supports Python 3.12 and 3.13 on "
        "Linux and macOS). Install with: uv add band-sdk[nooa]",
        name=error.name,
    ) from error

from typing_extensions import Unpack

from band.adapters.nooa.tools import (
    RoomTools,
    RoomToolSpec,
    custom_tool_spec,
    platform_tool_spec,
    room_tools_class,
    room_tools_reference,
    validate_custom_tool_names,
)
from band.converters.nooa import NooaHistoryConverter, NooaTranscript
from band.core.adapterconfig import BaseAdapterConfig
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
    TurnUsage,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import get_band_tool_category, iter_tool_definitions

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
    # NOOA's __init_subclass__ resets the execution config of every subclass to
    # the default unless it is passed again; its other class settings inherit.
    return cast(
        type[NooaRoomAgent],
        type(agent_class)(
            agent_class.__name__,
            (agent_class,),
            namespace,
            execution=agent_class._execution_config,
        ),
    )


class TurnObserver:
    """NOOA instrumentation hooks that record every model call of one turn.

    A turn can delegate to child agents, and each NOOA agent has its own event
    bus, so watching only the room's agent would miss their calls. NOOA scopes
    hooks to a ``ContextVar``, so while the turn runs every agent it calls passes
    through ``before_agent_call``, which subscribes that agent's bus. Hooks that
    were already installed keep receiving every call.
    """

    def __init__(self, wrapped: InstrumentationHooks | None) -> None:
        self._wrapped = wrapped
        self._unsubscribe: dict[int, Callable[[], None]] = {}
        self._closed = False
        self.usage = TurnUsage()
        self.reasoning: list[str] = []

    def observe(self, agent: Agent) -> None:
        """Record *agent*'s model calls until the turn closes."""
        bus = agent.event_manager
        if self._closed or id(bus) in self._unsubscribe:
            return
        self._unsubscribe[id(bus)] = bus.on(LLMComplete.__name__, self._record)

    def close(self) -> None:
        self._closed = True
        for unsubscribe in self._unsubscribe.values():
            unsubscribe()
        self._unsubscribe.clear()

    def before_agent_call(self, agent: Agent, **kwargs: Any) -> Any:
        self.observe(agent)
        if self._wrapped is None:
            return None
        return self._wrapped.before_agent_call(agent=agent, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Forward every other hook; without wrapped hooks, the rest are no-ops."""
        if self._wrapped is not None:
            return getattr(self._wrapped, name)
        if name.startswith(("before_", "after_")):
            return ignore_hook
        raise AttributeError(name)

    def _record(self, event: EventBase) -> None:
        if not isinstance(event, LLMComplete):
            return
        self.usage += TurnUsage(
            input_tokens=event.prompt_tokens,
            output_tokens=event.completion_tokens,
            cache_read_tokens=event.cached_tokens,
        )
        if reasoning := event.reasoning_content.strip():
            self.reasoning.append(reasoning)


def ignore_hook(*args: Any, **kwargs: Any) -> None:
    """A hook that observes nothing."""


@contextmanager
def observing_turn(agent: NooaRoomAgent) -> Iterator[TurnObserver]:
    """Observe *agent* and every agent it calls for the rest of this block."""
    previous = get_hooks()
    observer = TurnObserver(previous)
    observer.observe(agent)
    # NOOA calls hooks by name (getattr), so the forwarding __getattr__ covers
    # the protocol methods TurnObserver does not define itself.
    set_hooks(cast(InstrumentationHooks, observer))
    try:
        yield observer
    finally:
        set_hooks(previous)
        observer.close()


async def report_failure(tools: AgentToolsProtocol) -> None:
    """Report a failed turn without letting the report replace its cause."""
    try:
        await tools.send_failure(
            AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
        )
    except Exception:
        logger.exception("Failed to report the NOOA turn failure")


async def report_thoughts(tools: AgentToolsProtocol, reasoning: list[str]) -> None:
    """Post each model call's reasoning as a thought event, in order."""
    for text in reasoning:
        try:
            await tools.send_event(content=text, message_type=MessageType.THOUGHT)
        except Exception as error:  # noqa: BLE001 -- thought events are best-effort telemetry
            logger.warning("Failed to send thought event: %s", error)


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
    """Run a NOOA agent class in Band rooms, one agent instance per room.

    ``Emit.THOUGHTS`` posts each model call's **raw** ``reasoning_content`` as a
    thought event, which can surface chain-of-thought. Like every supported
    emit it is on when ``emit`` is omitted; pass an ``emit`` without it to keep
    reasoning out of the room.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.USAGE}
    )
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
        with observing_turn(agent) as observer:
            try:
                async with agent.room.bound_to(tools):
                    await agent.respond(message)
            except Exception:
                logger.exception("Room %s: NOOA turn failed", room_id)
                await report_failure(tools)
                raise
            finally:
                if Emit.THOUGHTS in self.features.emit:
                    await report_thoughts(tools, observer.reasoning)
                await self.emit_usage(tools, observer.usage)

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's agent when Band removes the adapter from a room."""
        if self._agents.pop(room_id, None) is not None:
            logger.debug("Room %s: Cleaned up NOOA agent", room_id)
