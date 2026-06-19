"""Agno adapter using the SimpleAdapter pattern."""

from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, ParamSpec, TypeVar

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.tool_filter import filter_tool_schemas
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
)
from band.converters.agno import (
    AgnoHistoryConverter,
    AgnoMessages,
    agno_function_class,
    agno_message_class,
)
from band.runtime.prompts import BASE_INSTRUCTIONS, CONTACT_SECTION, MEMORY_SECTION
from band.runtime.tools import get_band_tool_category

if TYPE_CHECKING:
    from agno.agent import Agent as AgnoAgent
    from agno.models.message import Message
    from agno.run.agent import RunOutput
    from agno.tools.function import Function

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

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


def _with_agent(
    fn: Callable[Concatenate[Any, AgnoAgent, P], Awaitable[R]],
) -> Callable[Concatenate[Any, P], Awaitable[R]]:
    @wraps(fn)
    async def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
        agent = getattr(self, "_agent", None)
        if agent is None:
            raise RuntimeError("AgnoAdapter was used before on_started()")
        return await fn(self, agent, *args, **kwargs)

    return wrapper


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
    """Bridge a developer-built Agno agent to Band.

    Note on replies: unlike the other adapters (which deliver only when the
    agent calls ``band_send_message``), this adapter falls back to posting the
    agent's final text itself, addressed to the message sender, when
    ``band_send_message`` was not called. Steer the agent to call the tool when
    you need explicit recipients or no auto-reply.

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
        agent: AgnoAgent | None = None,
        *,
        agent_factory: Callable[[], AgnoAgent] | None = None,
        history_converter: AgnoHistoryConverter | None = None,
        features: AdapterFeatures | None = None,
        session_id_factory: Callable[[str], str] = lambda room_id: room_id,
    ) -> None:
        """Bridge a developer-built Agno agent to Band.

        Provide **exactly one** of ``agent`` or ``agent_factory``:

        - ``agent``: a fully configured Agno agent. The adapter runs against
          ``agent.deep_copy()`` so the caller's instance stays immutable.
        - ``agent_factory``: a zero-arg callable returning a fresh Agno agent.
          The adapter calls it once at startup, avoiding ``deep_copy()``
          overhead for callers that can cheaply mint a new agent::

              adapter = AgnoAdapter(
                  agent_factory=lambda: AgnoAgent(
                      model=Claude(id="claude-sonnet-4-6"),
                      instructions="You are helpful.",
                  )
              )

        Args:
            session_id_factory: Maps a Band ``room_id`` to the Agno
                ``session_id`` used for that room's runs. Defaults to using the
                ``room_id`` itself, so each Band room is an isolated Agno
                session. This **overrides** any ``session_id`` configured on
                the agent. Consequence: Agno DB history previously stored under
                the agent's original ``session_id`` is no longer reused (runs
                are keyed by ``room_id``). To keep a single shared session
                across rooms, pass e.g. ``session_id_factory=lambda _r: "fixed"``.
        """
        if agent is not None and agent_factory is not None:
            raise ValueError(
                "AgnoAdapter accepts `agent` or `agent_factory`, not both."
            )
        if agent is not None:
            # Run against a copy so the caller's configured agent stays immutable.
            factory: Callable[[], AgnoAgent] = agent.deep_copy
        elif agent_factory is not None:
            factory = agent_factory
        else:
            raise ValueError(
                "AgnoAdapter requires exactly one of `agent` or `agent_factory`."
            )

        super().__init__(
            history_converter=history_converter or AgnoHistoryConverter(),
            features=features,
        )

        # The runtime agent is built once at startup (deep-copy or factory call),
        # deferring any factory invocation out of __init__.
        self._agent_factory = factory
        self._agent: AgnoAgent | None = None
        self._session_id_factory = session_id_factory

        # Running per-room transcripts; bootstrap history seeds each room.
        self._message_history: dict[str, list[Message]] = {}
        # Band tools are wired additively onto the single shared agent: the tool
        # set is the union of what any room has needed so far. Tracking wired
        # names keeps wiring idempotent (no duplicates, never removed).
        self._wired_tool_names: set[str] = set()
        # Built Functions cached by their only dynamic input (include_contacts),
        # so the schema build runs at most twice for the process lifetime rather
        # than on every message. Entrypoints are room-agnostic, so the cached
        # list is safe to reuse across rooms.
        self._band_tools_cache: dict[bool, list[Function]] = {}

        # Resolved against the runtime agent in on_started, once it exists.
        self._agno_manages_history = False

    @property
    def agent(self) -> AgnoAgent | None:
        """The running Agno agent, initialized in on_started."""
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
        """Build the runtime agent and sync the converter identity.

        The runtime agent is produced by the factory captured at construction —
        either the caller's ``agent.deep_copy`` or a developer ``agent_factory``.
        Agent-dependent checks run here (not in ``__init__``) so the factory is
        only ever invoked at startup.
        """
        await super().on_started(agent_name, agent_description)

        self._agent = self._agent_factory()
        self._agno_manages_history = self._detect_agno_history(self._agent)
        self._warn_on_memory_collision(self._agent)

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
        """Run the developer's Agno agent and ensure a reply is sent."""
        logger.info(
            "Room %s msg %s: handling from %s (sender=%s, bootstrap=%s)",
            room_id,
            msg.id,
            msg.sender_name or msg.sender_type,
            msg.sender_id,
            is_session_bootstrap,
        )

        self._ensure_band_tools(tools)
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
        if Emit.EXECUTION in self.features.emit:
            await self._report_tool_executions(
                response, tools, room_id=room_id, msg_id=msg.id
            )

        self._persist_turn(room_id, response)
        await self._send_reply(msg, tools, response, room_id=room_id)

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
        message_cls = agno_message_class()
        messages = self._prior_transcript(
            history, is_session_bootstrap=is_session_bootstrap, room_id=room_id
        )

        if participants_msg:
            messages.append(
                message_cls(role="user", content=f"[System]: {participants_msg}")
            )
        if contacts_msg:
            messages.append(
                message_cls(role="user", content=f"[System]: {contacts_msg}")
            )
        messages.append(message_cls(role="user", content=msg.format_for_llm()))
        return messages

    @_with_agent
    async def _run_agent(
        self,
        agent: AgnoAgent,
        messages: list[Message],
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> RunOutput | None:
        """Run the Agno agent with the room's tools bound for this call."""
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

    @classmethod
    async def _send_reply(
        cls,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        response: RunOutput,
        *,
        room_id: str,
    ) -> None:
        """Send final text unless the agent already posted through Band.

        The shared base prompt tells the agent "plain text output is not
        delivered" to steer it toward ``band_send_message`` (proper mentions +
        events). This adapter still delivers final text here as a fallback
        convenience for agents that return text directly; the fallback is
        intentionally not advertised in the prompt.
        """
        if any(
            _tool_name(execution) == "band_send_message"
            for execution in _tool_executions(response)
        ):
            logger.debug(
                "Room %s msg %s: agent replied via band_send_message", room_id, msg.id
            )
            return

        text = response.get_content_as_string().strip()
        if not text:
            logger.debug("Room %s msg %s: agent produced no reply", room_id, msg.id)
            return

        # Address the reply to the sender. ``sender_id`` is the primary
        # identifier, but it may not match a cached participant (id-space
        # mismatch or a stale cache); fall back to the display name. An
        # unresolvable mention raises ValueError in mention resolution, which
        # would otherwise fail the whole turn — try each candidate and degrade
        # to a warning rather than crashing.
        candidates = [c for c in (msg.sender_id, msg.sender_name) if c]
        for candidate in candidates:
            try:
                await tools.send_message(text, mentions=[candidate])
            except ValueError as e:
                logger.debug(
                    "Room %s msg %s: mention %r did not resolve: %s",
                    room_id,
                    msg.id,
                    candidate,
                    e,
                )
            else:
                logger.info(
                    "Room %s msg %s: sent reply (%d chars), mention=%s",
                    room_id,
                    msg.id,
                    len(text),
                    candidate,
                )
                return

        logger.warning(
            "Room %s msg %s: no resolvable mention for sender %s (%s); reply not delivered",
            room_id,
            msg.id,
            msg.sender_id,
            msg.sender_name,
        )

    def _ensure_band_tools(self, tools: AgentToolsProtocol) -> None:
        """Additively wire this room's Band tools onto the shared agent.

        The agent accumulates the union of tools any room has needed. Wiring is
        idempotent by name: a tool already wired (e.g. from an earlier room) is
        not re-added. This means once a contact-hub room is seen, contact tool
        schemas remain visible in all rooms on the shared agent -- intentional,
        not strict per-room visibility. Execution stays room-correct regardless
        because each tool entrypoint routes through the current room's
        AgentTools via the ``_current_tools`` ContextVar.
        """
        if self._agent is None:
            return

        # The built Function set depends only on whether contacts are included
        # (memory inclusion and feature filters are static), so cache on that
        # flag and avoid rebuilding schemas every message. Wiring stays
        # idempotent by name, so cache reuse never double-adds a tool.
        include_contacts = Capability.CONTACTS in self.features.capabilities or bool(
            getattr(tools, "is_hub_room", False)
        )
        functions = self._band_tools_cache.get(include_contacts)
        if functions is None:
            functions = self._build_band_tools(tools, include_contacts=include_contacts)
            self._band_tools_cache[include_contacts] = functions

        new_tools = [fn for fn in functions if fn.name not in self._wired_tool_names]
        wired: list[str] = []
        for fn in new_tools:
            try:
                self._agent.add_tool(fn)
                self._wired_tool_names.add(fn.name)
                wired.append(fn.name)
            except RuntimeError as e:
                logger.warning("Could not wire Band tool %s: %s", fn.name, e)
        if wired:
            logger.info(
                "Wired %d Band tool(s) into Agno agent: %s",
                len(wired),
                ", ".join(wired),
            )

    def _inject_band_instructions(self) -> None:
        """Append Band tool guidance to the runtime agent's ``additional_context``.

        Appending (rather than replacing) preserves the developer's own
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
        """Compose Band guidance gated on enabled capabilities."""
        parts: list[str] = [BASE_INSTRUCTIONS.strip()]
        if Capability.MEMORY in self.features.capabilities:
            parts.append(MEMORY_SECTION.strip())
        if Capability.CONTACTS in self.features.capabilities:
            parts.append(CONTACT_SECTION.strip())
        return "\n\n".join(parts)

    def _build_band_tools(
        self, tools: AgentToolsProtocol, *, include_contacts: bool
    ) -> list[Function]:
        """Convert Band tool schemas into Agno Functions.

        Honors the AdapterFeatures include/exclude/category filters via
        :func:`filter_tool_schemas`. ``include_contacts`` is resolved by the
        caller (CONTACTS capability or a contact-hub room, mirroring LangGraph)
        so the built set can be cached on that flag.
        """
        function_cls = agno_function_class()
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
                    function_cls(
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

    async def _report_tool_executions(
        self,
        response: RunOutput,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Emit tool_call/tool_result events for reportable executions."""
        executions = [
            execution
            for execution in _tool_executions(response)
            if _tool_name(execution) not in _SELF_REPORTING_TOOLS
        ]
        if not executions:
            return

        logger.info(
            "Room %s msg %s: reporting %d tool execution(s)",
            room_id,
            msg_id,
            len(executions),
        )
        for execution in executions:
            await self._emit_execution(execution, tools, room_id=room_id, msg_id=msg_id)

    @classmethod
    async def _emit_execution(
        cls,
        execution: Any,
        tools: AgentToolsProtocol,
        *,
        room_id: str,
        msg_id: str,
    ) -> None:
        """Emit the tool_call + tool_result event pair for one tool execution."""
        tool_call_id = getattr(execution, "tool_call_id", None) or ""
        tool_name = getattr(execution, "tool_name", None) or ""
        tool_args = getattr(execution, "tool_args", None) or {}
        is_error = bool(getattr(execution, "tool_call_error", False))
        result = str(getattr(execution, "result", "") or "")

        logger.debug(
            "Room %s msg %s: tool %s(%s) -> %s%s",
            room_id,
            msg_id,
            tool_name,
            tool_args,
            result[:200],
            " [error]" if is_error else "",
        )
        try:
            await tools.send_event(
                content=json.dumps(
                    {"name": tool_name, "args": tool_args, "tool_call_id": tool_call_id}
                ),
                message_type="tool_call",
            )
            await tools.send_event(
                content=json.dumps(
                    {
                        "name": tool_name,
                        "output": result,
                        "tool_call_id": tool_call_id,
                        "is_error": is_error,
                    }
                ),
                message_type="tool_result",
            )
        except Exception as e:
            logger.warning(
                "Room %s msg %s: failed to report tool execution %s: %s",
                room_id,
                msg_id,
                tool_name,
                e,
            )
