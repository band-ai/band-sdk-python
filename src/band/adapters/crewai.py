"""CrewAI adapter using SimpleAdapter pattern with official CrewAI SDK.

The CrewAI BaseTool wrappers and the sync-to-async bridge live in
``band.integrations.crewai`` so that both this adapter and the experimental
``CrewAIFlowAdapter`` share one implementation. See that package for the
nest_asyncio process-global warning and the tool builder contract.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ClassVar

from band_sdk_core import AgentFailure
from pydantic import PositiveInt
from typing_extensions import Unpack

from band.converters.crewai import CrewAIHistoryConverter, CrewAIMessages
from band.core.adapterconfig import BaseAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE, AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability, Emit, FeatureKwargs, PlatformMessage
from band.integrations.crewai import (
    CrewAIToolContext,
    EmitToolCallsReporter,
    ReplyTracker,
    build_band_crewai_tools,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import BandTool

if TYPE_CHECKING:
    from crewai import Agent as CrewAIAgent
    from crewai.tools import BaseTool

logger = logging.getLogger(__name__)

_PROVIDER = "crewai"


# Context variable for thread-safe room context access.
# Set automatically when processing messages, accessed by tools.
_current_room_context: ContextVar[tuple[str, AgentToolsProtocol] | None] = ContextVar(
    "_current_room_context", default=None
)

# Per-turn tool activity: whether any tool ran and what band_send_message
# posted. Set in on_message, written by the tool wrappers (through
# _get_context). Shared by reference so the marks are visible across CrewAI's
# sync-to-async tool execution boundary.
_reply_tracker_var: ContextVar[ReplyTracker | None] = ContextVar(
    "_crewai_reply_tracker", default=None
)


# CrewAI offers no error type or code for an empty completion, so its message
# is the only discriminator. One definition, matched here and faked in tests.
EMPTY_LLM_RESPONSE_MARKER = "Invalid response from LLM call"


def _is_empty_llm_response(exc: Exception) -> bool:
    """Whether ``exc`` is CrewAI reporting that an LLM call came back empty.

    ``crewai.utilities.agent_utils`` raises this bare ``ValueError`` for every
    empty completion in its loop, not only the forced final-answer step — so a
    match means "no text came back", never "the turn is healthy".
    """
    return isinstance(exc, ValueError) and EMPTY_LLM_RESPONSE_MARKER in str(exc)


def _silence_lite_agent_error_panel() -> None:
    """Deregister CrewAI's benign red "LiteAgent Failed" console panel.

    This agent answers only through band_send_message, so most turns end on an
    empty completion and CrewAI's global console listener prints an alarming
    panel anyway (regardless of verbose). Remove only that handler; tracing and
    genuine errors are untouched. Idempotent (a later call finds nothing) and
    best-effort (leave the panel if CrewAI internals move).
    """
    try:
        # event_listener is imported for its side effect: registering the handlers.
        from crewai.events import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv
            crewai_event_bus,
        )
        from crewai.events.event_listener import (  # noqa: F401, PLC0415 -- crewai extra, absent from the standard dev venv
            event_listener,
        )
        from crewai.events.types.agent_events import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv
            LiteAgentExecutionErrorEvent,
        )

        handlers = crewai_event_bus._sync_handlers.get(
            LiteAgentExecutionErrorEvent, frozenset()
        )
        for handler in list(handlers):
            if getattr(handler, "__name__", "") == "on_lite_agent_execution_error":
                crewai_event_bus.off(LiteAgentExecutionErrorEvent, handler)
    except (ImportError, AttributeError) as e:
        # Tolerate only CrewAI's private event internals moving on a version bump
        # (we reach into _sync_handlers); any other error is a real bug — let it raise.
        # warn (not debug): this means our private-API reach broke and the silencer
        # needs updating — surface it rather than bury it.
        logger.warning("Could not silence CrewAI LiteAgent error panel: %s", e)


class CrewAIAdapterConfig(BaseAdapterConfig):
    """Settings for :class:`CrewAIAdapter`.

    Attributes:
        model: CrewAI LLM model name (e.g. ``"gpt-5.4"``); API keys are read
            from the environment by CrewAI's ``LLM`` class.
        role: The agent's role in the crew; ``None`` uses the agent's name.
        goal: The agent's objective; ``None`` uses the agent's description.
        backstory: The agent's background; the platform instructions are
            appended to it.
        custom_section: Extra instructions added to the platform prompt.
        verbose: Enables CrewAI's detailed logging.
        max_iter: Maximum reasoning iterations per turn.
        max_rpm: Maximum LLM requests per minute; ``None`` is unlimited.
        allow_delegation: Whether CrewAI may delegate to other crew agents.
    """

    model: str = "gpt-5.4"
    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
    custom_section: str | None = None
    verbose: bool = False
    max_iter: PositiveInt = 20
    max_rpm: PositiveInt | None = None
    allow_delegation: bool = False


class CrewAIAdapter(SimpleAdapter[CrewAIMessages]):
    """CrewAI adapter using the official CrewAI SDK.

    Integrates the CrewAI framework (https://docs.crewai.com/) with Band
    platform for building collaborative multi-agent systems.

    Example:
        adapter = CrewAIAdapter(
            CrewAIAdapterConfig(
                model="gpt-5.4",
                role="Research Assistant",
                goal="Help users find and analyze information",
                backstory="Expert researcher with deep knowledge across domains",
            )
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.TOOL_CALLS})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: CrewAIAdapterConfig | None = None,
        *,
        history_converter: CrewAIHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ):
        """Initialize the CrewAI adapter.

        Args:
            config: Agent settings; defaults to ``CrewAIAdapterConfig()``.
            history_converter: Custom history converter (optional).
            additional_tools: Custom tools as ``(InputModel, callable)``
                tuples; the callable may be sync or async.
        """
        super().__init__(
            history_converter=history_converter or CrewAIHistoryConverter(),
            **features,
        )

        self.config = config or CrewAIAdapterConfig()
        self._crewai_agent: CrewAIAgent | None = None
        self._message_history: dict[str, list[dict[str, Any]]] = {}
        self._custom_tools: list[CustomToolDef] = additional_tools or []
        self._tool_loop: asyncio.AbstractEventLoop | None = None

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Initialize CrewAI agent after metadata is fetched."""
        try:
            from crewai import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv
                LLM,
            )
            from crewai import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev venv
                Agent as CrewAIAgent,
            )
        except ImportError as e:
            raise ImportError(
                "crewai is required for CrewAI adapter.\n"
                "Install with: pip install 'band-sdk[crewai]'\n"
                "Or: uv add crewai nest-asyncio"
            ) from e

        _silence_lite_agent_error_panel()

        await super().on_started(agent_name, agent_description)
        self._tool_loop = asyncio.get_running_loop()

        role = self.config.role or agent_name
        goal = (
            self.config.goal or agent_description or "Help users accomplish their tasks"
        )

        if self.config.backstory:
            # User provided full backstory -- append capability-gated platform
            # instructions so the LLM knows about memory/contact tools if enabled.
            platform_prompt = render_system_prompt(
                agent_name=agent_name,
                agent_description=agent_description,
                custom_section=self.config.custom_section or "",
                features=self.features,
            )
            backstory = f"{self.config.backstory}\n\n{platform_prompt}"
        else:
            backstory = render_system_prompt(
                agent_name=agent_name,
                agent_description=agent_description,
                custom_section=self.config.custom_section or "",
                features=self.features,
            )

        tools = self.create_crewai_tools()

        self._crewai_agent = CrewAIAgent(
            role=role,
            goal=goal,
            backstory=backstory,
            llm=LLM(model=self.config.model),
            tools=tools,
            verbose=self.config.verbose,
            max_iter=self.config.max_iter,
            max_rpm=self.config.max_rpm,
            allow_delegation=self.config.allow_delegation,
        )

        logger.info(
            "CrewAI adapter started for agent: %s (model=%s, role=%s)",
            agent_name,
            self.config.model,
            role,
        )

    def _get_context(self) -> CrewAIToolContext | None:
        """Snapshot the current room context for the shared tool builder.

        Returns CrewAIToolContext when called inside ``on_message``,
        otherwise None (which the shared wrapper translates into a
        ``"No room context available"`` error JSON).
        """
        ctx = _current_room_context.get()
        if ctx is None:
            return None
        room_id, tools = ctx
        return CrewAIToolContext(
            room_id=room_id, tools=tools, reply_tracker=_reply_tracker_var.get()
        )

    def create_crewai_tools(self) -> list[BaseTool]:
        """Build the CrewAI BaseTool list via the shared integrations builder.

        The wrappers, the sync-to-async bridge, and the execution-event
        reporter all live in ``band.integrations.crewai``. This adapter
        supplies its own context getter (reading the legacy
        ``_current_room_context`` ContextVar), its own reporter
        (gated by ``Emit.TOOL_CALLS``), and its event loop fallback.
        """
        return build_band_crewai_tools(
            get_context=self._get_context,
            reporter=EmitToolCallsReporter(self.features),
            features=self.features,
            custom_tools=self._custom_tools,
            fallback_loop=self._tool_loop,
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CrewAIMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle incoming message using CrewAI agent."""
        logger.debug("Handling message %s in room %s", msg.id, room_id)

        if not self._crewai_agent:
            message = "CrewAI agent not initialized - ensure on_started() was called"
            await tools.send_failure(AgentFailure(_PROVIDER, message))
            raise RuntimeError(message)

        # Set context variable for tool access (thread-safe room context).
        # Wrap in try/finally immediately to ensure cleanup even if code
        # before the main try block raises an exception.
        reply_tracker = ReplyTracker()
        _current_room_context.set((room_id, tools))
        _reply_tracker_var.set(reply_tracker)
        try:
            await self._process_message(
                msg=msg,
                tools=tools,
                history=history,
                participants_msg=participants_msg,
                contacts_msg=contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                room_id=room_id,
                reply_tracker=reply_tracker,
            )
        finally:
            # Clear context after processing to prevent stale context in async
            # environments with task reuse
            _current_room_context.set(None)
            _reply_tracker_var.set(None)

    async def _process_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CrewAIMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
        reply_tracker: ReplyTracker,
    ) -> None:
        """Internal message processing logic."""
        assert self._crewai_agent is not None, "on_message already checked this"
        if is_session_bootstrap:
            if history:
                self._message_history[room_id] = [
                    {"role": h["role"], "content": h["content"]} for h in history
                ]
                logger.info(
                    "Room %s: Loaded %s historical messages",
                    room_id,
                    len(history),
                )
            else:
                self._message_history[room_id] = []
                logger.info("Room %s: No historical messages found", room_id)
        elif room_id not in self._message_history:
            self._message_history[room_id] = []

        sections: list[str] = []

        if self._message_history.get(room_id):
            history_text = "\n".join(
                f"{m['role']}: {m['content']}" for m in self._message_history[room_id]
            )
            sections.append(
                "[Earlier conversation in this room -- already handled, for "
                f"context only. Do not repeat any action described in it:]\n{history_text}"
            )

        if participants_msg:
            sections.append(f"[System]: {participants_msg}")
            logger.info("Room %s: Participants updated", room_id)

        if contacts_msg:
            sections.append(f"[System]: {contacts_msg}")
            logger.info("Room %s: Contacts broadcast received", room_id)

        user_message = msg.format_for_llm()
        sections.append(f"[New message -- act on this now:]\n{user_message}")

        self._message_history[room_id].append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        total_messages = len(self._message_history[room_id])
        logger.info(
            "Room %s: Processing with %s messages (first_msg=%s)",
            room_id,
            total_messages,
            is_session_bootstrap,
        )

        # CrewAI usage is intentionally not emitted (Emit.USAGE absent from
        # SUPPORTED_EMIT): result.usage_metrics is cumulative-lifetime, not
        # per-turn. Proper per-turn capture is deferred — don't add emit_usage here.
        try:
            prompt = "\n\n".join(sections)
            result = await self._kickoff_with_empty_response_retry(
                self._crewai_agent, prompt, reply_tracker, room_id
            )

        except Exception:
            logger.exception("Error processing message")
            await tools.send_failure(
                AgentFailure(_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
            )
            raise

        self._message_history[room_id].extend(
            {
                "role": "assistant",
                "content": f"[sent via {BandTool.SEND_MESSAGE}] {post}",
            }
            for post in reply_tracker.posts
        )
        final_text = (result.raw or "") if result else ""
        if final_text:
            self._message_history[room_id].append(
                {
                    "role": "assistant",
                    "content": final_text,
                }
            )

        logger.info(
            "Room %s: CrewAI turn over for %s (output=%s chars, history=%s)",
            room_id,
            msg.id,
            len(final_text),
            len(self._message_history[room_id]),
        )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when agent leaves a room."""
        if room_id in self._message_history:
            del self._message_history[room_id]
            logger.debug("Room %s: Cleaned up CrewAI session", room_id)

    async def _kickoff_with_empty_response_retry(
        self,
        agent: CrewAIAgent,
        prompt: str,
        reply_tracker: ReplyTracker,
        room_id: str,
    ) -> Any:
        """Retry an empty kickoff once only when no tool activity was recorded."""
        for attempt in range(2):
            try:
                result = await agent.kickoff_async(prompt)
            except Exception as exc:
                if not _is_empty_llm_response(exc):
                    raise
                logger.debug("Room %s: CrewAI returned no text: %s", room_id, exc)
                result = None

            if result and (result.raw or "").strip():
                return result
            if reply_tracker.any_tool_ran:
                return None
            if attempt == 0:
                logger.info(
                    "Room %s: CrewAI kickoff returned no text before any tool "
                    "ran; retrying",
                    room_id,
                )

        logger.warning("Room %s: CrewAI retry returned no text; giving up", room_id)
        return None
