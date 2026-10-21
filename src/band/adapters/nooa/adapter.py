"""``NooaAdapter``: one NOOA agent instance per Band room.

A NOOA agent is a Python class whose ``...``-bodied async methods are completed
by an LLM that writes and runs Python against ``self``. Band tools reach the
model as async methods on a live ``self.room`` object (see ``tools``), not as
JSON tool schemas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from nooa import Agent, Context
from nooa.context_blocks import EventBase
from nooa.events import LLMComplete
from nooa.runtime.hooks import InstrumentationHooks, get_hooks, set_hooks
from typing_extensions import Unpack

from band.adapters.nooa.agent import NooaRoomAgent
from band.adapters.nooa.tools import (
    RoomTools,
    TurnBinding,
    custom_tool_spec,
    platform_tool_spec,
    room_tools_class,
    room_tools_reference,
    validate_custom_tool_names,
)
from band.converters.nooa import NooaHistoryConverter, NooaTranscript
from band.core.adapterconfig import BaseAdapterConfig
from band.core.handoff import context_handoff
from band.core.protocols import AgentToolsProtocol, generic_provider_failure
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    TurnUsage,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.formatters import format_current_inputs
from band.runtime.prompts import render_system_prompt, with_system_updates
from band.runtime.tools import BandTool, selected_tool_definitions

logger = logging.getLogger(__name__)

_PROVIDER = "nooa"

INSTRUCTIONS_BLOCK = "band_instructions"
TOOLS_BLOCK = "band_tools"
TRANSCRIPT_BLOCK = "band_room_transcript"

ROOM_TOOLS_SECTION = f"""\
## Band tools in Python
Every Band tool named in these instructions is an async method on `self.room`,
called from `execute_python` with keyword arguments, for example
`await self.room.{BandTool.SEND_MESSAGE}(content="...", mentions=["..."])`.
Custom tools instead accept their original Pydantic payload as `arguments={{...}}`;
use the validation aliases and payload structure in their reference.
Tool validation or execution failures raise `BandToolError` with the reason.
An image file read with
`{BandTool.READ_ROOM_FILE}` comes back as a list of `Image` objects; `show(image)`
lets you see one. After you reply or decline, finish with `return_result(None)`."""


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


@dataclass(frozen=True)
class RoomSurface:
    """The tools one kind of room offers, and the agent class documenting them."""

    tools_class: type[RoomTools]
    agent_class: type[NooaRoomAgent]
    reference: str


# Every hook NOOA may call on an InstrumentationHooks object. Anything else,
# such as the optional activate_agent_call extension, must stay absent.
HOOK_NAMES = frozenset(
    name for name in vars(InstrumentationHooks) if not name.startswith("_")
)


class TurnObserver:
    """NOOA instrumentation hooks that record every model call of one turn.

    A turn can delegate to child agents, and each NOOA agent has its own event
    bus, so watching only the room's agent would miss their calls. NOOA scopes
    hooks to a ``ContextVar``, so while the turn runs every agent it calls passes
    through ``before_agent_call``, which subscribes that agent's bus. Hooks that
    were already installed keep receiving every call. Each call's reasoning goes
    to ``on_reasoning`` as soon as the call completes.
    """

    def __init__(
        self,
        wrapped: InstrumentationHooks | None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._on_reasoning = on_reasoning
        self._unsubscribe: dict[int, Callable[[], None]] = {}
        self._closed = False
        self.usage = TurnUsage()

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
        if name in HOOK_NAMES:
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
        if self._on_reasoning is not None and (
            reasoning := event.reasoning_content.strip()
        ):
            self._on_reasoning(reasoning)


def ignore_hook(*args: Any, **kwargs: Any) -> None:
    """A hook that observes nothing."""


@contextmanager
def observing_turn(
    agent: NooaRoomAgent, *, on_reasoning: Callable[[str], None] | None = None
) -> Iterator[TurnObserver]:
    """Observe *agent* and every agent it calls for the rest of this block."""
    previous = get_hooks()
    observer = TurnObserver(previous, on_reasoning)
    observer.observe(agent)
    # NOOA calls hooks by name (getattr), so the forwarding __getattr__ covers
    # the protocol methods TurnObserver does not define itself.
    set_hooks(cast(InstrumentationHooks, observer))
    try:
        yield observer
    finally:
        set_hooks(previous)
        observer.close()


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
    thought event as soon as that call completes, ahead of the tool calls its
    code makes. This can surface chain-of-thought. Like every supported
    emit it is on when ``emit`` is omitted; pass an ``emit`` without it to keep
    reasoning out of the room.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )
    supports_batch_input: ClassVar[bool] = True

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
        # Keyed by whether the room is the contacts hub, which always gets the
        # contact tools; filled in on_started, once the features are final.
        self._surfaces: dict[bool, RoomSurface] = {}
        self._instructions = ""
        self._agents: dict[str, NooaRoomAgent] = {}

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Build the room tool surfaces once the effective features are known."""
        await super().on_started(agent_name, agent_description)
        self._instructions = render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section or "",
            features=self.features,
            extra_sections=(ROOM_TOOLS_SECTION,),
        )
        self._surfaces = {
            is_hub_room: self._room_surface(is_hub_room=is_hub_room)
            for is_hub_room in (False, True)
        }
        logger.info("NOOA adapter started for agent: %s", agent_name)

    def _room_surface(self, *, is_hub_room: bool) -> RoomSurface:
        """The tools the features allow in this kind of room, then the custom tools.

        This controls what the model is told about and what ``self.room``
        offers. It is not an access boundary: generated code runs in-process
        and can reach the turn's tools object directly.
        """
        specs = [
            platform_tool_spec(definition)
            for definition in selected_tool_definitions(
                self.features, is_hub_room=is_hub_room
            )
        ] + [custom_tool_spec(tool_def) for tool_def in self._custom_tools]
        tools_class = room_tools_class(specs)
        return RoomSurface(
            tools_class=tools_class,
            agent_class=with_room_tools(self._agent_class, tools_class),
            reference=room_tools_reference(tools_class),
        )

    def _new_room_agent(
        self, transcript: NooaTranscript, *, is_hub_room: bool
    ) -> NooaRoomAgent:
        if not self._surfaces:
            raise RuntimeError("NooaAdapter.on_started must run before any message")
        surface = self._surfaces[is_hub_room]
        agent = surface.agent_class()
        agent.room = surface.tools_class(
            emit_tool_calls=Emit.TOOL_CALLS in self.features.emit
        )
        agent.context[INSTRUCTIONS_BLOCK] = Context(
            value=self._instructions, prefix=True
        )
        agent.context[TOOLS_BLOCK] = Context(value=surface.reference, prefix=True)
        if transcript:
            agent.context[TRANSCRIPT_BLOCK] = Context(
                value="\n".join(transcript), prefix=True
            )
        return agent

    def _room_agent(
        self,
        room_id: str,
        transcript: NooaTranscript,
        *,
        is_session_bootstrap: bool,
        is_hub_room: bool,
    ) -> NooaRoomAgent:
        """The room's agent, recreated from platform history at session start."""
        if is_session_bootstrap:
            self._agents.pop(room_id, None)
        if room_id not in self._agents:
            self._agents[room_id] = self._new_room_agent(
                transcript, is_hub_room=is_hub_room
            )
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
        """Run one room message as a turn of one."""
        await self.on_turn(
            (msg,),
            tools,
            history,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: NooaTranscript,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run a turn's room messages through one call of the agent's ``respond``."""
        with context_handoff() as handoff:
            agent = self._room_agent(
                room_id,
                history,
                is_session_bootstrap=is_session_bootstrap,
                is_hub_room=tools.is_hub_room,
            )
            message = with_system_updates(
                format_current_inputs(messages), participants_msg, contacts_msg
            )
            observer: TurnObserver | None = None
            try:
                # Usage includes owned cleanup; the closed binding refuses late thoughts.
                with ExitStack() as observation:
                    async with agent.room.bound_to(tools) as binding:
                        observer = observation.enter_context(
                            observing_turn(
                                agent, on_reasoning=self._thought_poster(binding)
                            )
                        )
                        handoff.consume()
                        await agent.respond(message)
            except Exception as error:
                if not handoff.consumed:
                    raise
                if tools.turn.complete:
                    # A settled turn must not invite a duplicate delivery.
                    logger.warning(
                        "Room %s: NOOA failed after the turn was settled: %s",
                        room_id,
                        error,
                    )
                    return
                logger.exception("Room %s: NOOA turn failed", room_id)
                await tools.send_failure(generic_provider_failure(_PROVIDER, error))
                raise
            finally:
                if observer is not None:
                    await self.emit_usage(tools, observer.usage)

    def _thought_poster(self, binding: TurnBinding) -> Callable[[str], None] | None:
        """Where the turn's reasoning goes: the room, only with ``Emit.THOUGHTS``."""
        return binding.post_thought if Emit.THOUGHTS in self.features.emit else None

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's agent when Band removes the adapter from a room."""
        if self._agents.pop(room_id, None) is not None:
            logger.debug("Room %s: Cleaned up NOOA agent", room_id)
