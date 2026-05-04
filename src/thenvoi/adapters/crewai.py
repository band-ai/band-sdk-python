"""CrewAI adapter using SimpleAdapter pattern with official CrewAI SDK.

The CrewAI BaseTool wrappers and the sync-to-async bridge live in
``thenvoi.integrations.crewai`` so that both this adapter and the experimental
``CrewAIFlowAdapter`` share one implementation. See that package for the
nest_asyncio process-global warning and the tool builder contract.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from contextvars import ContextVar
from typing import ClassVar, Any, Literal

try:
    from crewai import Agent as CrewAIAgent
    from crewai import LLM
    from crewai.tools import BaseTool
except ImportError as e:
    raise ImportError(
        "crewai is required for CrewAI adapter.\n"
        "Install with: pip install 'thenvoi-sdk[crewai]'\n"
        "Or: uv add crewai nest-asyncio"
    ) from e

from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.converters.crewai import CrewAIHistoryConverter, CrewAIMessages
from thenvoi.integrations.crewai import (
    CrewAIToolContext,
    EmitExecutionReporter,
    build_thenvoi_crewai_tools,
)
from thenvoi.runtime.custom_tools import CustomToolDef
from thenvoi.runtime.prompts import render_system_prompt

logger = logging.getLogger(__name__)


# Context variable for thread-safe room context access.
# Set automatically when processing messages, accessed by tools.
_current_room_context: ContextVar[tuple[str, AgentToolsProtocol] | None] = ContextVar(
    "_current_room_context", default=None
)

MessageType = Literal["thought", "error", "task"]


class CrewAIAdapter(SimpleAdapter[CrewAIMessages]):
    """CrewAI adapter using the official CrewAI SDK.

    Integrates the CrewAI framework (https://docs.crewai.com/) with Thenvoi
    platform for building collaborative multi-agent systems.

    Example:
        adapter = CrewAIAdapter(
            model="gpt-4o",
            role="Research Assistant",
            goal="Help users find and analyze information",
            backstory="Expert researcher with deep knowledge across domains",
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()

    Note:
        API keys are configured through environment variables as expected by
        the CrewAI LLM class (e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY).
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str = "gpt-4o",
        role: str | None = None,
        goal: str | None = None,
        backstory: str | None = None,
        custom_section: str | None = None,
        enable_execution_reporting: bool = False,
        enable_memory_tools: bool = False,
        verbose: bool = False,
        max_iter: int = 20,
        max_rpm: int | None = None,
        allow_delegation: bool = False,
        history_converter: CrewAIHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        system_prompt: str | None = None,  # Deprecated
        features: AdapterFeatures | None = None,
    ):
        """Initialize the CrewAI adapter.

        Args:
            model: Model name (e.g., "gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet").
                   API keys are read from environment variables by CrewAI's LLM class.
            role: Agent's role in the crew (e.g., "Research Assistant")
            goal: Agent's primary goal or objective
            backstory: Agent's background and expertise description
            custom_section: Custom instructions added to the agent's backstory
            enable_execution_reporting: If True, sends tool_call/tool_result events
            verbose: If True, enables detailed logging from CrewAI
            max_iter: Maximum iterations for the agent (default: 20)
            max_rpm: Maximum requests per minute (rate limiting)
            allow_delegation: Whether to allow task delegation to other agents
            history_converter: Custom history converter (optional)
            additional_tools: List of custom tools as (InputModel, callable) tuples.
                Each InputModel is a Pydantic model defining the tool's input schema,
                and the callable is the function to execute (sync or async).
            system_prompt: Deprecated. Use 'backstory' instead for prompt customization.
        """
        if system_prompt is not None:
            warnings.warn(
                "The 'system_prompt' parameter is deprecated and will be removed in a "
                "future version. Use 'backstory' parameter instead for prompt "
                "customization. The CrewAI SDK uses role/goal/backstory pattern.",
                DeprecationWarning,
                stacklevel=2,
            )
            # If backstory not provided, use system_prompt as backstory for compatibility
            if backstory is None:
                backstory = system_prompt

        # --- Deprecation shim: boolean → features migration ---
        _has_legacy_booleans = enable_execution_reporting or enable_memory_tools
        if _has_legacy_booleans and features is not None:
            raise ThenvoiConfigError(
                "Cannot pass both legacy boolean flags "
                "(enable_execution_reporting / enable_memory_tools) and 'features'. "
                "Use features=AdapterFeatures(...) instead."
            )

        if _has_legacy_booleans:
            warnings.warn(
                "enable_execution_reporting and enable_memory_tools are deprecated. "
                "Use features=AdapterFeatures(emit={Emit.EXECUTION}, "
                "capabilities={Capability.MEMORY}) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # NOTE: unlike ClaudeSDK, CrewAI's legacy enable_execution_reporting
            # maps to {Emit.EXECUTION} only (no THOUGHTS). CrewAI had no native
            # thought emission under this flag, so migrating to THOUGHTS would
            # turn on a new behavior, not preserve existing behavior.
            features = AdapterFeatures(
                emit=frozenset({Emit.EXECUTION})
                if enable_execution_reporting
                else frozenset(),
                capabilities=frozenset({Capability.MEMORY})
                if enable_memory_tools
                else frozenset(),
            )

        super().__init__(
            history_converter=history_converter or CrewAIHistoryConverter(),
            features=features,
        )

        self.model = model
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.custom_section = custom_section
        self.verbose = verbose
        self.max_iter = max_iter
        self.max_rpm = max_rpm
        self.allow_delegation = allow_delegation

        self._crewai_agent: CrewAIAgent | None = None
        self._message_history: dict[str, list[dict[str, Any]]] = {}
        self._custom_tools: list[CustomToolDef] = additional_tools or []
        self._tool_loop: asyncio.AbstractEventLoop | None = None

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Initialize CrewAI agent after metadata is fetched."""
        await super().on_started(agent_name, agent_description)
        self._tool_loop = asyncio.get_running_loop()

        role = self.role or agent_name
        goal = self.goal or agent_description or "Help users accomplish their tasks"

        if self.backstory:
            # User provided full backstory -- append capability-gated platform
            # instructions so the LLM knows about memory/contact tools if enabled.
            platform_prompt = render_system_prompt(
                agent_name=agent_name,
                agent_description=agent_description,
                custom_section=self.custom_section or "",
                features=self.features,
            )
            backstory = f"{self.backstory}\n\n{platform_prompt}"
        else:
            backstory = render_system_prompt(
                agent_name=agent_name,
                agent_description=agent_description,
                custom_section=self.custom_section or "",
                features=self.features,
            )

        tools = self.create_crewai_tools()

        self._crewai_agent = CrewAIAgent(
            role=role,
            goal=goal,
            backstory=backstory,
            llm=LLM(model=self.model),
            tools=tools,
            verbose=self.verbose,
            max_iter=self.max_iter,
            max_rpm=self.max_rpm,
            allow_delegation=self.allow_delegation,
        )

        logger.info(
            "CrewAI adapter started for agent: %s (model=%s, role=%s)",
            agent_name,
            self.model,
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
        return CrewAIToolContext(room_id=room_id, tools=tools)

    def create_crewai_tools(self) -> list[BaseTool]:
        """Build the CrewAI BaseTool list via the shared integrations builder.

        The wrappers, the sync-to-async bridge, and the execution-event
        reporter all live in ``thenvoi.integrations.crewai``. This adapter
        supplies its own context getter (reading the legacy
        ``_current_room_context`` ContextVar), its own reporter
        (gated by ``Emit.EXECUTION``), and its event loop fallback.
        """
        return build_thenvoi_crewai_tools(
            get_context=self._get_context,
            reporter=EmitExecutionReporter(self.features),
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
            raise RuntimeError(
                "CrewAI agent not initialized - ensure on_started() was called"
            )

        # Set context variable for tool access (thread-safe room context).
        # Wrap in try/finally immediately to ensure cleanup even if code
        # before the main try block raises an exception.
        _current_room_context.set((room_id, tools))
        try:
            await self._process_message(
                msg=msg,
                tools=tools,
                history=history,
                participants_msg=participants_msg,
                contacts_msg=contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                room_id=room_id,
            )
        finally:
            # Clear context after processing to prevent stale context in async
            # environments with task reuse
            _current_room_context.set(None)

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
    ) -> None:
        """Internal message processing logic."""
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

        messages = []

        if is_session_bootstrap and self._message_history.get(room_id):
            history_text = "\n".join(
                f"{m['role']}: {m['content']}" for m in self._message_history[room_id]
            )
            messages.append(
                {
                    "role": "user",
                    "content": f"[Previous conversation:]\n{history_text}",
                }
            )

        if participants_msg:
            messages.append(
                {
                    "role": "user",
                    "content": f"[System]: {participants_msg}",
                }
            )
            logger.info("Room %s: Participants updated", room_id)

        if contacts_msg:
            messages.append(
                {
                    "role": "user",
                    "content": f"[System]: {contacts_msg}",
                }
            )
            logger.info("Room %s: Contacts broadcast received", room_id)

        user_message = msg.format_for_llm()
        messages.append({"role": "user", "content": user_message})

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

        try:
            # Type ignore explanation: CrewAI's kickoff_async is typed to accept
            # only a string prompt, but the implementation also accepts a list of
            # message dicts (similar to OpenAI's messages format) for multi-turn
            # context. This is documented behavior but the type stubs haven't been
            # updated. See: https://docs.crewai.com/concepts/agents
            result = await self._crewai_agent.kickoff_async(messages)  # type: ignore[arg-type]

            if result and result.raw:
                self._message_history[room_id].append(
                    {
                        "role": "assistant",
                        "content": result.raw,
                    }
                )

            logger.info(
                "Room %s: CrewAI agent completed (output_length=%s)",
                room_id,
                len(result.raw) if result and result.raw else 0,
            )

        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)
            await self._report_error(tools, str(e))
            raise

        logger.debug(
            "Message %s processed successfully (history now has %s messages)",
            msg.id,
            len(self._message_history[room_id]),
        )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when agent leaves a room."""
        if room_id in self._message_history:
            del self._message_history[room_id]
            logger.debug("Room %s: Cleaned up CrewAI session", room_id)

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception as e:
            logger.warning("Failed to send error event: %s", e)
