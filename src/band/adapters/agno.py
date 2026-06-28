"""Agno adapter using the SimpleAdapter pattern."""

from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ClassVar

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
)
from band.converters.agno import AgnoHistoryConverter, AgnoMessages
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import get_band_tool_category

try:
    from agno.models.message import Message
    from agno.run.agent import (
        RunOutput,
        ToolCallCompletedEvent,
        ToolCallStartedEvent,
    )
    from agno.tools import Toolkit
    from agno.tools.function import Function
    from agno.utils.callables import ainvoke_callable_factory, is_callable_factory
except ImportError as e:
    raise ImportError(
        "agno is required for the Agno adapter.\n"
        "Install with: pip install 'band-sdk[agno]'"
    ) from e

if TYPE_CHECKING:
    from agno.agent import Agent as AgnoAgent
    from agno.run.agent import RunOutputEvent

logger = logging.getLogger(__name__)

# These tools already produce visible room output.
_SELF_REPORTING_TOOLS = frozenset({"band_send_message", "band_send_event"})

# Conversation roles to persist across turns. Allowlisting these drops Agno's
# per-run injected messages (system/developer instructions, datetime/state
# context, summaries) so they are not replayed alongside freshly injected ones.
_CONVERSATION_ROLES = frozenset({"user", "assistant", "tool"})

# Current room tools for wired Agno tool entrypoints.
_current_tools: ContextVar[AgentToolsProtocol | None] = ContextVar(
    "agno_current_tools", default=None
)


def _tool_executions(response: RunOutput) -> list[Any]:
    return list(getattr(response, "tools", None) or [])


def _tool_name(execution: Any) -> str:
    return getattr(execution, "tool_name", None) or ""


def _make_band_entrypoint(tool_name: str) -> Callable[..., Awaitable[str]]:
    async def _entrypoint(**kwargs: Any) -> str:
        active = _current_tools.get()
        if active is None:
            return f"Error: no active Band context for tool {tool_name}"
        result = await active.execute_tool_call(tool_name, kwargs)
        return result if isinstance(result, str) else json.dumps(result, default=str)

    _entrypoint.__name__ = tool_name
    return _entrypoint


@contextmanager
def _bind_room_tools(tools: AgentToolsProtocol) -> Iterator[None]:
    """Bind room tools for one Agno run."""
    token = _current_tools.set(tools)
    try:
        yield
    finally:
        _current_tools.reset(token)


class AgnoAdapter(SimpleAdapter[AgnoMessages]):
    """Bridge a user-built Agno agent to Band.

    "User" throughout this adapter means the SDK integrator who built and
    configured the Agno agent — never a chat end-user (``sender_type`` "User").

    Note on replies: like the other adapters, this one delivers nothing on its
    own — the agent must call ``band_send_message`` to communicate. The base
    prompt states "plain text output is not delivered"; an agent that only
    returns plain text stays silent. It is up to the agent (the LLM) to decide
    whether to respond and whom to address.

    Note on ``Emit.THOUGHTS``: when enabled, the agent's **raw**
    ``reasoning_content`` is posted to the room as a thought event. This can
    surface chain-of-thought and intermediate context, so it is strictly
    opt-in — enable it only when that visibility is intended.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.THOUGHTS}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        agent: AgnoAgent,
        *,
        history_converter: AgnoHistoryConverter | None = None,
        features: AdapterFeatures | None = None,
        session_id_factory: Callable[[str], str] = lambda room_id: room_id,
    ) -> None:
        """Bridge a user-built Agno agent to Band.

        The adapter runs against the ``agent`` instance you pass **directly**.
        At startup it configures that instance for Band -- replacing its
        ``tools`` with a per-run factory, disabling ``cache_callables``, and
        appending Band guidance to ``additional_context``. The adapter therefore
        takes ownership of the agent; do not reuse the same instance elsewhere.

        Args:
            agent: A fully configured Agno agent to bridge to Band.
            session_id_factory: Maps a Band ``room_id`` to the Agno
                ``session_id`` used for that room's runs. Defaults to using the
                ``room_id`` itself, so each Band room is an isolated Agno
                session. This **overrides** any ``session_id`` configured on
                the agent. Consequence: Agno DB history previously stored under
                the agent's original ``session_id`` is no longer reused (runs
                are keyed by ``room_id``). To keep a single shared session
                across rooms, pass e.g. ``session_id_factory=lambda _r: "fixed"``.
        """
        super().__init__(
            history_converter=history_converter or AgnoHistoryConverter(),
            features=features,
        )

        # The caller's agent is used directly. It becomes the runtime agent
        # (self._agent) in on_started, where the agent-dependent Band
        # configuration is applied -- deferring that work out of __init__.
        self._given_agent = agent
        self._agent: AgnoAgent | None = None
        self._session_id_factory = session_id_factory

        # Running per-room transcripts; bootstrap history seeds each room.
        self._message_history: dict[str, list[Message]] = {}
        # Band tools are exposed per-run via a callable-tools factory installed on
        # the shared agent (see _resolve_room_tools), so each room's run offers
        # exactly its own tool set -- no cross-room schema leakage. The user's own
        # tools (those they configured on the agent, captured at startup) are
        # re-included on every run, and may be either a static list or a per-run
        # callable factory.
        self._user_tools: list[Any] | Callable[..., Any] = []
        # Built Functions cached by their only dynamic input (include_contacts),
        # so the schema build runs at most twice for the process lifetime rather
        # than on every run. Entrypoints route through the _current_tools
        # ContextVar, so the cached list is safe to reuse across rooms.
        self._band_tools_cache: dict[bool, list[Function]] = {}

        # Resolved against the runtime agent in on_started, once it exists.
        self._agno_manages_history = False

    @property
    def agent(self) -> AgnoAgent | None:
        """The Agno agent this adapter runs against, set in on_started."""
        return self._agent

    def _detect_agno_history(self, agent: AgnoAgent) -> bool:
        """Detect whether Agno persists and replays its own history.

        Agno loads prior runs into context only when ``add_history_to_context``
        is set *and* a database is attached (without a ``db`` the feature is
        inert). When it does, Band must not also rehydrate platform history into
        the run input, or the two history sources collide and contaminate the
        model context. Band still keeps its own per-turn transcript store; it
        simply stops feeding it back into the run.
        """
        manages = bool(
            getattr(agent, "add_history_to_context", False)
            and getattr(agent, "db", None) is not None
        )
        if manages:
            warnings.warn(
                "This Agno agent manages its own conversation history "
                "(add_history_to_context=True with a database). Band's history "
                "rehydration is disabled to avoid contaminating the context; "
                "Agno will replay prior turns from its database via session "
                "persistence.",
                UserWarning,
                stacklevel=3,
            )
        return manages

    def _warn_on_memory_collision(self, agent: AgnoAgent) -> None:
        """Warn when Band and Agno memory are both enabled."""
        if Capability.MEMORY not in self.features.capabilities:
            return

        enabled: list[str] = []
        if agent.update_memory_on_run:
            enabled.append("update_memory_on_run")
        if agent.enable_agentic_memory:
            enabled.append("enable_agentic_memory")

        if enabled:
            warnings.warn(
                "Capability.MEMORY exposes Band memory tools to the agent, but "
                f"this Agno agent also manages its own memory ({', '.join(enabled)}). "
                "The two memory systems collide; disable one of them.",
                UserWarning,
                stacklevel=3,
            )

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Configure the caller's agent for Band and sync the converter identity.

        The adapter runs against the agent passed at construction. Agent-dependent
        checks and the Band configuration (tool factory, ``additional_context``)
        run here, not in ``__init__``, so they happen once at startup.
        """
        await super().on_started(agent_name, agent_description)

        agent = self._given_agent
        self._agent = agent
        self._agno_manages_history = self._detect_agno_history(agent)
        self._warn_on_memory_collision(agent)

        # Install per-run tool resolution: capture the user's own tools, then
        # replace ``agent.tools`` with our factory so each run offers exactly the
        # active room's tool set (see _resolve_room_tools). Disable Agno's
        # callable-tools cache so the factory runs every turn regardless of
        # session_id; we cache the built Functions ourselves in _band_tools_cache.
        self._capture_user_tools(agent)
        agent.cache_callables = False
        # Agno's `tools` type annotation lists only sync factories, but its
        # resolver (ainvoke_callable_factory) explicitly supports async ones, and
        # the adapter only ever runs via async `arun`.
        agent.tools = self._resolve_room_tools  # type: ignore[assignment]

        # Band guidance is composed purely from static capabilities, so inject it
        # once here -- before any room runs -- rather than lazily on first message.
        self._inject_band_instructions()

        # Converter identity (used to map this agent's own past messages to the
        # assistant role) is synced by SimpleAdapter.on_started above, which
        # calls set_agent_name on any converter that defines it.

        logger.info("Agno adapter started for agent: %s", agent_name)
        logger.debug(
            "Agno adapter features: emit=%s capabilities=%s",
            sorted(e.value for e in self.features.emit),
            sorted(c.value for c in self.features.capabilities),
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: AgnoMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run the user's Agno agent and ensure a reply is sent."""
        logger.info(
            "Room %s msg %s: handling from %s (sender=%s, bootstrap=%s)",
            room_id,
            msg.id,
            msg.sender_name or msg.sender_type,
            msg.sender_id,
            is_session_bootstrap,
        )

        messages = self._build_run_input(
            msg,
            history,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )
        response = await self._run_agent(
            messages, tools, room_id=room_id, msg_id=msg.id
        )
        if response is None:
            return

        if Emit.THOUGHTS in self.features.emit:
            await self._report_thoughts(response, tools, room_id=room_id, msg_id=msg.id)

        self._persist_turn(room_id, response)

        if not any(
            _tool_name(execution) == "band_send_message"
            for execution in _tool_executions(response)
        ):
            logger.debug(
                "Room %s msg %s: agent did not call band_send_message; "
                "nothing delivered",
                room_id,
                msg.id,
            )

    async def on_cleanup(self, room_id: str) -> None:
        """Drop the room's accumulated transcript when the agent leaves."""
        self._message_history.pop(room_id, None)

    def _prior_transcript(
        self,
        history: AgnoMessages,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> list[Message]:
        """Committed prior-turn messages that seed this run, as a fresh list.

        ``_message_history[room_id]`` is the *committed* record of prior turns.
        It is written only at commit points — here (seeding rehydrated platform
        history on bootstrap) and in :meth:`_persist_turn` (after a successful
        run). A *copy* is returned so the caller composes this turn's input
        without mutating the committed transcript; otherwise a failed or
        message-less run would leave the injected system/user messages behind to
        be replayed on the next turn.

        When the Agno agent manages its own history this returns empty — Agno
        replays prior turns from its database.
        """
        if self._agno_manages_history:
            return []
        if is_session_bootstrap:
            self._message_history[room_id] = list(history)
        return list(self._message_history.setdefault(room_id, []))

    def _build_run_input(
        self,
        msg: PlatformMessage,
        history: AgnoMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> list[Message]:
        """Compose this turn's Agno input: prior transcript + injected messages.

        Built from a *copy* of the committed transcript (see
        :meth:`_prior_transcript`), so building the input never mutates
        ``_message_history`` and a failed run leaves no injected residue behind.
        """
        messages = self._prior_transcript(
            history, is_session_bootstrap=is_session_bootstrap, room_id=room_id
        )

        if participants_msg:
            messages.append(
                Message(role="user", content=f"[System]: {participants_msg}")
            )
        if contacts_msg:
            messages.append(Message(role="user", content=f"[System]: {contacts_msg}"))
        messages.append(Message(role="user", content=msg.format_for_llm()))
        return messages

    async def _run_agent(
        self,
        messages: list[Message],
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> RunOutput | None:
        """Run the Agno agent with the room's tools bound for this call.

        When ``Emit.EXECUTION`` is enabled the run is streamed so tool_call /
        tool_result events are emitted *as each tool runs* (see
        :meth:`_run_streamed`), matching the other adapters' live reporting.
        Otherwise it runs non-streaming, exactly as before.
        """
        agent = self._agent
        if agent is None:
            raise RuntimeError("AgnoAdapter was used before on_started()")
        session_id = self._session_id_factory(room_id)
        logger.debug(
            "Room %s msg %s: running Agno agent (%d input messages, session_id=%s)",
            room_id,
            msg_id,
            len(messages),
            session_id,
        )
        try:
            with _bind_room_tools(tools):
                if Emit.EXECUTION in self.features.emit:
                    response = await self._run_streamed(
                        agent,
                        messages,
                        tools,
                        session_id=session_id,
                        room_id=room_id,
                        msg_id=msg_id,
                    )
                else:
                    response = await agent.arun(input=messages, session_id=session_id)
        except Exception:
            # Keep the user-facing payload generic; the full traceback is in the
            # agent log via logger.exception. Exception text can include DB
            # strings, paths, and tokens that must not surface in chat.
            logger.exception(
                "Room %s msg %s: error running Agno agent", room_id, msg_id
            )
            try:
                await tools.send_event(
                    content="Internal error while processing message; see agent logs.",
                    message_type="error",
                )
            except Exception:
                logger.exception(
                    "Room %s msg %s: failed to report error event", room_id, msg_id
                )
            raise

        if response is None:
            logger.debug(
                "Room %s msg %s: Agno agent returned no response", room_id, msg_id
            )
        return response

    async def _run_streamed(
        self,
        agent: AgnoAgent,
        messages: list[Message],
        tools: AgentToolsProtocol,
        *,
        session_id: str,
        room_id: str,
        msg_id: str,
    ) -> RunOutput | None:
        """Stream the run, emitting tool events live, and return the final output.

        ``stream_events=True`` yields a ``ToolCallStartedEvent`` /
        ``ToolCallCompletedEvent`` for every tool call (user-configured and
        Band), and ``yield_run_output=True`` yields the assembled ``RunOutput``
        last. The ``_current_tools`` binding from :meth:`_run_agent` spans the
        whole iteration, since tools execute as the stream is consumed.
        """
        final: RunOutput | None = None
        async for item in agent.arun(
            input=messages,
            session_id=session_id,
            stream=True,
            stream_events=True,
            yield_run_output=True,
        ):
            if isinstance(item, RunOutput):
                final = item
            else:
                await self._emit_stream_event(
                    item, tools, room_id=room_id, msg_id=msg_id
                )
        return final

    def _persist_turn(self, room_id: str, response: RunOutput) -> None:
        """Persist Agno's transcript, keeping only conversation messages.

        Allowlisting conversation roles drops Agno's per-run injected messages
        (instructions, context, summaries) so they are not replayed alongside
        the freshly injected ones on the next run.
        """
        if response.messages:
            self._message_history[room_id] = [
                m for m in response.messages if m.role in _CONVERSATION_ROLES
            ]

    def _capture_user_tools(self, agent: AgnoAgent) -> None:
        """Capture the user's own tools before installing the room factory.

        Replacing ``agent.tools`` with our per-run factory (see
        :meth:`_resolve_room_tools`) would otherwise drop whatever tools the user
        configured, so we stash them and re-include them on every run. A
        user-supplied *callable* tools factory is kept as-is and resolved per run
        with Agno's own semantics; a static list is copied.
        """
        tools: Any = getattr(agent, "tools", None)
        if tools is None:
            self._user_tools = []
        elif is_callable_factory(tools, excluded_types=(Toolkit, Function)):
            self._user_tools = tools  # a per-run callable factory
        else:
            self._user_tools = list(tools)  # a static list

    async def _resolve_room_tools(self, run_context: Any = None) -> list[Any]:
        """Per-run tool factory: user tools + the active room's Band tools.

        Installed as ``agent.tools`` in :meth:`on_started`. Agno invokes it once
        per run (its own cache disabled) via ``ainvoke_callable_factory`` and
        resolves the result into that run's context rather than mutating shared
        agent state -- so concurrent rooms never see each other's tools. The
        active room is read from the ``_current_tools`` ContextVar bound around
        ``arun`` in :meth:`_run_agent` (the same binding that routes tool
        execution), keeping visibility and execution aligned. Band tools are
        gated per room: the CONTACTS capability or a contact-hub room includes
        the contact tools, so a normal room never sees them even after a hub room
        has run.
        """
        user_tools = await self._resolve_user_tools(run_context)

        active = _current_tools.get()
        if active is None:
            # Outside a bound run we cannot know the room; expose only the user's
            # own tools rather than guessing Band tool visibility.
            return user_tools

        include_contacts = Capability.CONTACTS in self.features.capabilities or bool(
            getattr(active, "is_hub_room", False)
        )
        band = self._band_tools_cache.get(include_contacts)
        if band is None:
            band = self._build_band_tools(active, include_contacts=include_contacts)
            self._band_tools_cache[include_contacts] = band
        return [*user_tools, *band]

    async def _resolve_user_tools(self, run_context: Any) -> list[Any]:
        """Resolve the user's own tools for this run.

        A static list is returned as a fresh copy; a user-supplied callable
        factory is invoked with Agno's own signature injection
        (agent/run_context/session_state) and may be sync or async.
        """
        if not callable(self._user_tools):
            return list(self._user_tools)

        resolved = await ainvoke_callable_factory(
            self._user_tools, self._agent, run_context
        )
        return list(resolved) if resolved else []

    def _inject_band_instructions(self) -> None:
        """Append Band tool guidance to the runtime agent's ``additional_context``.

        Appending (rather than replacing) preserves the user's own
        instructions. Called once at startup, before any room runs.
        """
        if self._agent is None:
            return

        guidance = self._band_instructions()
        existing = getattr(self._agent, "additional_context", None)
        self._agent.additional_context = (
            f"{existing}\n\n{guidance}" if existing else guidance
        )

    def _band_instructions(self) -> str:
        """Compose the Band identity + guidance gated on enabled capabilities.

        Mirrors the other adapters via :func:`render_system_prompt`: prepends
        "You are {name}, {description}." so the model knows its Band-registered
        identity, then the base instructions and any capability sections. The
        developer's own Agno ``instructions`` are preserved separately — this is
        appended to ``additional_context`` (see :meth:`_inject_band_instructions`).
        """
        return render_system_prompt(
            agent_name=self.agent_name or "Agent",
            agent_description=self.agent_description or "An AI assistant",
            features=self.features,
        )

    def _build_band_tools(
        self, tools: AgentToolsProtocol, *, include_contacts: bool
    ) -> list[Function]:
        """Convert Band tool schemas into Agno Functions.

        Honors the AdapterFeatures include/exclude/category filters via
        :func:`filter_tool_schemas`. ``include_contacts`` is resolved by the
        caller (CONTACTS capability or a contact-hub room, mirroring LangGraph)
        so the built set can be cached on that flag.
        """
        schemas = tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=include_contacts,
        )
        schemas = filter_tool_schemas(
            schemas,
            self.features,
            get_name=lambda s: s.get("function", {}).get("name", ""),
            get_category=lambda s: get_band_tool_category(
                s.get("function", {}).get("name", "")
            ),
        )

        band_tools: list[Function] = []
        for schema in schemas:
            fn = schema.get("function", {})
            if name := fn.get("name"):
                band_tools.append(
                    Function(
                        name=name,
                        description=fn.get("description", "") or "",
                        parameters=fn.get("parameters")
                        or {"type": "object", "properties": {}},
                        entrypoint=_make_band_entrypoint(name),
                        skip_entrypoint_processing=True,
                    )
                )
        return band_tools

    @classmethod
    async def _report_thoughts(
        cls,
        response: RunOutput,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Post Agno reasoning as a thought event."""
        reasoning = getattr(response, "reasoning_content", None)
        text = (reasoning or "").strip() if isinstance(reasoning, str) else ""
        if not text:
            return

        logger.info(
            "Room %s msg %s: reporting reasoning as thought (%d chars)",
            room_id,
            msg_id,
            len(text),
        )
        try:
            await tools.send_event(content=text, message_type="thought")
        except Exception as e:
            logger.warning(
                "Room %s msg %s: failed to report thought: %s", room_id, msg_id, e
            )

    @classmethod
    async def _emit_stream_event(
        cls,
        item: RunOutputEvent,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Emit a tool_call / tool_result event for one streamed run event.

        Agno yields a started + completed event (each carrying a
        ``ToolExecution``) for every tool call -- user-configured and Band
        alike. Self-reporting tools already produce visible room output, so they
        are skipped. The completed event carries ``result`` + ``tool_call_error``,
        so exactly one tool_result is emitted per call whether it succeeded or
        failed; all other events (content deltas, reasoning, ``ToolCallErrorEvent``)
        fall through and are ignored.
        """
        if (
            isinstance(item, ToolCallStartedEvent)
            and (ex := item.tool) is not None
            and ex.tool_name not in _SELF_REPORTING_TOOLS
        ):
            await cls._emit_tool_event(
                tools,
                "tool_call",
                {
                    "name": ex.tool_name or "",
                    "args": ex.tool_args or {},
                    "tool_call_id": ex.tool_call_id or "",
                },
                room_id=room_id,
                msg_id=msg_id,
            )
        elif (
            isinstance(item, ToolCallCompletedEvent)
            and (ex := item.tool) is not None
            and ex.tool_name not in _SELF_REPORTING_TOOLS
        ):
            await cls._emit_tool_event(
                tools,
                "tool_result",
                {
                    "name": ex.tool_name or "",
                    "output": str(ex.result or ""),
                    "tool_call_id": ex.tool_call_id or "",
                    "is_error": bool(ex.tool_call_error),
                },
                room_id=room_id,
                msg_id=msg_id,
            )

    @staticmethod
    async def _emit_tool_event(
        tools: AgentToolsProtocol,
        message_type: str,
        payload: dict[str, Any],
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Send one tool event, logging (never raising) on failure."""
        logger.debug("Room %s msg %s: %s %s", room_id, msg_id, message_type, payload)
        try:
            await tools.send_event(
                content=json.dumps(payload), message_type=message_type
            )
        except Exception as e:
            logger.warning(
                "Room %s msg %s: failed to report %s %s: %s",
                room_id,
                msg_id,
                message_type,
                payload.get("name"),
                e,
            )
