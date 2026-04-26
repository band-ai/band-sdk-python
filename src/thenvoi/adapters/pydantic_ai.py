"""
Pydantic AI adapter using SimpleAdapter pattern.

Extracted from thenvoi.integrations.pydantic_ai.agent.ThenvoiPydanticAgent.
"""

from __future__ import annotations

import json
import logging
import warnings
from typing import ClassVar, Any, Callable

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RunContext,
)
from pydantic_ai.messages import (
    ModelRequest,
    UserPromptPart,
)

from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.converters.pydantic_ai import (
    PydanticAIHistoryConverter,
    PydanticAIMessages,
)
from thenvoi.runtime.prompts import render_system_prompt
from thenvoi.runtime.tools import get_tool_description

logger = logging.getLogger(__name__)


class PydanticAIAdapter(SimpleAdapter[PydanticAIMessages]):
    """
    Pydantic AI adapter using SimpleAdapter pattern.

    Uses Pydantic AI's Agent for LLM interactions,
    with platform tools registered via @agent.tool decorators.

    Example:
        adapter = PydanticAIAdapter(
            model="openai:gpt-4o",
            custom_section="You are a helpful assistant.",
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str,
        system_prompt: str | None = None,
        custom_section: str | None = None,
        enable_execution_reporting: bool = False,
        enable_memory_tools: bool = False,
        history_converter: PydanticAIHistoryConverter | None = None,
        additional_tools: list[Callable[..., Any]] | None = None,
        features: AdapterFeatures | None = None,
    ):
        """
        Initialize the Pydantic AI adapter.

        Args:
            model: Pydantic AI model string (e.g., "openai:gpt-4o", "anthropic:claude-3-5-sonnet-latest")
            system_prompt: Optional custom system prompt (overrides default)
            custom_section: Optional custom section added to default system prompt
            enable_execution_reporting: Deprecated. Use features=AdapterFeatures(emit={Emit.EXECUTION}).
            enable_memory_tools: Deprecated. Use features=AdapterFeatures(capabilities={Capability.MEMORY}).
            history_converter: Optional custom history converter
            additional_tools: Optional list of PydanticAI-compatible tool functions.
                Each function should follow PydanticAI's tool signature:
                `def my_tool(ctx: RunContext[AgentToolsProtocol], arg1: str, ...) -> T`
                These are registered via agent.tool() alongside platform tools.
            features: Shared adapter feature settings (capabilities, emit, tool filters).
        """
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
            features = AdapterFeatures(
                emit=frozenset({Emit.EXECUTION})
                if enable_execution_reporting
                else frozenset(),
                capabilities=frozenset({Capability.MEMORY})
                if enable_memory_tools
                else frozenset(),
            )

        super().__init__(
            history_converter=history_converter or PydanticAIHistoryConverter(),
            features=features,
        )

        self.model = model
        self.system_prompt = system_prompt
        self.custom_section = custom_section
        self._system_prompt: str | None = None

        self._agent: Agent[AgentToolsProtocol, None] | None = None
        # Conversation history per room (Pydantic AI is stateless, we maintain state)
        self._message_history: dict[str, list] = {}
        # Custom tools (PydanticAI-compatible functions)
        self._custom_tools: list[Callable[..., Any]] = additional_tools or []

    # --- Adapted from ThenvoiPydanticAgent._on_started ---
    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Create the Pydantic AI agent after metadata is fetched."""
        await super().on_started(agent_name, agent_description)
        self._agent = self._create_agent()
        logger.info("Pydantic AI adapter started for agent: %s", agent_name)

    # --- Copied from ThenvoiPydanticAgent._create_agent ---
    def _create_agent(self) -> Agent[AgentToolsProtocol, None]:
        """Create Pydantic AI Agent with platform tools."""
        system = self.system_prompt or render_system_prompt(
            agent_name=self.agent_name,
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.custom_section or "",
            features=self.features,
        )
        self._system_prompt = system

        # We respond via tools only, so the model output is unused. Using `str`
        # (instead of `None`) keeps newer pydantic-ai-slim versions happy —
        # 1.87+ rejects `output_type=None` with `UserError("At least one output
        # type must be provided other than `None`")`.
        agent: Agent[AgentToolsProtocol, str] = Agent(
            self.model,
            system_prompt=system,
            deps_type=AgentToolsProtocol,
            output_type=str,
        )

        # Register platform tools dynamically from centralized definitions
        # All tools catch exceptions and return error strings so LLM can see failures

        async def thenvoi_send_message(
            ctx: RunContext[AgentToolsProtocol],
            content: str,
            mentions: list[str],
        ) -> dict[str, Any] | str:
            try:
                return await ctx.deps.send_message(content, mentions)
            except Exception as e:
                return f"Error sending message: {e}"

        thenvoi_send_message.__doc__ = get_tool_description("thenvoi_send_message")
        agent.tool(thenvoi_send_message)

        async def thenvoi_send_event(
            ctx: RunContext[AgentToolsProtocol],
            content: str,
            message_type: str,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any] | str:
            try:
                return await ctx.deps.send_event(content, message_type, metadata)
            except Exception as e:
                return f"Error sending event: {e}"

        thenvoi_send_event.__doc__ = get_tool_description("thenvoi_send_event")
        agent.tool(thenvoi_send_event)

        async def thenvoi_add_participant(
            ctx: RunContext[AgentToolsProtocol],
            identifier: str,
            role: str = "member",
        ) -> dict[str, Any] | str:
            try:
                return await ctx.deps.add_participant(identifier, role)
            except Exception as e:
                return f"Error adding participant '{identifier}': {e}"

        thenvoi_add_participant.__doc__ = get_tool_description(
            "thenvoi_add_participant"
        )
        agent.tool(thenvoi_add_participant)

        async def thenvoi_remove_participant(
            ctx: RunContext[AgentToolsProtocol],
            identifier: str,
        ) -> dict[str, Any] | str:
            try:
                return await ctx.deps.remove_participant(identifier)
            except Exception as e:
                return f"Error removing participant '{identifier}': {e}"

        thenvoi_remove_participant.__doc__ = get_tool_description(
            "thenvoi_remove_participant"
        )
        agent.tool(thenvoi_remove_participant)

        async def thenvoi_lookup_peers(
            ctx: RunContext[AgentToolsProtocol],
            page: int = 1,
            page_size: int = 50,
        ) -> dict[str, Any] | str:
            try:
                return await ctx.deps.lookup_peers(page, page_size)
            except Exception as e:
                return f"Error looking up peers: {e}"

        thenvoi_lookup_peers.__doc__ = get_tool_description("thenvoi_lookup_peers")
        agent.tool(thenvoi_lookup_peers)

        async def thenvoi_get_participants(
            ctx: RunContext[AgentToolsProtocol],
        ) -> list[dict[str, Any]] | str:
            try:
                return await ctx.deps.get_participants()
            except Exception as e:
                return f"Error getting participants: {e}"

        thenvoi_get_participants.__doc__ = get_tool_description(
            "thenvoi_get_participants"
        )
        agent.tool(thenvoi_get_participants)

        async def thenvoi_create_chatroom(
            ctx: RunContext[AgentToolsProtocol],
            task_id: str | None = None,
        ) -> str:
            try:
                return await ctx.deps.create_chatroom(task_id)
            except Exception as e:
                return f"Error creating chatroom (task_id={task_id}): {e}"

        thenvoi_create_chatroom.__doc__ = get_tool_description(
            "thenvoi_create_chatroom"
        )
        agent.tool(thenvoi_create_chatroom)

        # Contact management tools (opt-in via Capability.CONTACTS)
        if Capability.CONTACTS in self.features.capabilities:

            async def thenvoi_list_contacts(
                ctx: RunContext[AgentToolsProtocol],
                page: int = 1,
                page_size: int = 50,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.list_contacts(page, page_size)
                except Exception as e:
                    return f"Error listing contacts: {e}"

            thenvoi_list_contacts.__doc__ = get_tool_description(
                "thenvoi_list_contacts"
            )
            agent.tool(thenvoi_list_contacts)

            async def thenvoi_add_contact(
                ctx: RunContext[AgentToolsProtocol],
                handle: str,
                message: str | None = None,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.add_contact(handle, message)
                except Exception as e:
                    return f"Error adding contact '{handle}': {e}"

            thenvoi_add_contact.__doc__ = get_tool_description("thenvoi_add_contact")
            agent.tool(thenvoi_add_contact)

            async def thenvoi_remove_contact(
                ctx: RunContext[AgentToolsProtocol],
                handle: str | None = None,
                contact_id: str | None = None,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.remove_contact(handle, contact_id)
                except Exception as e:
                    return f"Error removing contact: {e}"

            thenvoi_remove_contact.__doc__ = get_tool_description(
                "thenvoi_remove_contact"
            )
            agent.tool(thenvoi_remove_contact)

            async def thenvoi_list_contact_requests(
                ctx: RunContext[AgentToolsProtocol],
                page: int = 1,
                page_size: int = 50,
                sent_status: str = "pending",
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.list_contact_requests(
                        page, page_size, sent_status
                    )
                except Exception as e:
                    return f"Error listing contact requests: {e}"

            thenvoi_list_contact_requests.__doc__ = get_tool_description(
                "thenvoi_list_contact_requests"
            )
            agent.tool(thenvoi_list_contact_requests)

            async def thenvoi_respond_contact_request(
                ctx: RunContext[AgentToolsProtocol],
                action: str,
                handle: str | None = None,
                request_id: str | None = None,
            ) -> dict[str, Any] | str:
                logger.info(
                    "thenvoi_respond_contact_request called: action=%s, handle=%s, request_id=%s",
                    action,
                    handle,
                    request_id,
                )
                try:
                    result = await ctx.deps.respond_contact_request(
                        action, handle, request_id
                    )
                    logger.info("thenvoi_respond_contact_request result: %s", result)
                    return result
                except Exception as e:
                    logger.error("thenvoi_respond_contact_request error: %s", e)
                    error_msg = f"Error responding to contact request: {e}"
                    # Auto-send error event so it's visible in the room
                    try:
                        await ctx.deps.send_event(error_msg, "error")
                    except Exception:
                        pass  # Don't fail if error reporting fails
                    return error_msg

            thenvoi_respond_contact_request.__doc__ = get_tool_description(
                "thenvoi_respond_contact_request"
            )
            agent.tool(thenvoi_respond_contact_request)

        # Memory management tools (enterprise only - opt-in)
        if Capability.MEMORY in self.features.capabilities:

            async def thenvoi_list_memories(
                ctx: RunContext[AgentToolsProtocol],
                subject_id: str | None = None,
                scope: str | None = None,
                system: str | None = None,
                type: str | None = None,
                segment: str | None = None,
                content_query: str | None = None,
                page_size: int = 50,
                status: str | None = None,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.list_memories(
                        subject_id=subject_id,
                        scope=scope,
                        system=system,
                        type=type,
                        segment=segment,
                        content_query=content_query,
                        page_size=page_size,
                        status=status,
                    )
                except Exception as e:
                    return f"Error listing memories: {e}"

            thenvoi_list_memories.__doc__ = get_tool_description(
                "thenvoi_list_memories"
            )
            agent.tool(thenvoi_list_memories)

            async def thenvoi_store_memory(
                ctx: RunContext[AgentToolsProtocol],
                content: str,
                system: str,
                type: str,
                segment: str,
                thought: str,
                scope: str = "subject",
                subject_id: str | None = None,
                metadata: dict[str, Any] | None = None,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.store_memory(
                        content=content,
                        system=system,
                        type=type,
                        segment=segment,
                        thought=thought,
                        scope=scope,
                        subject_id=subject_id,
                        metadata=metadata,
                    )
                except Exception as e:
                    return f"Error storing memory: {e}"

            thenvoi_store_memory.__doc__ = get_tool_description("thenvoi_store_memory")
            agent.tool(thenvoi_store_memory)

            async def thenvoi_get_memory(
                ctx: RunContext[AgentToolsProtocol],
                memory_id: str,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.get_memory(memory_id)
                except Exception as e:
                    return f"Error getting memory: {e}"

            thenvoi_get_memory.__doc__ = get_tool_description("thenvoi_get_memory")
            agent.tool(thenvoi_get_memory)

            async def thenvoi_supersede_memory(
                ctx: RunContext[AgentToolsProtocol],
                memory_id: str,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.supersede_memory(memory_id)
                except Exception as e:
                    return f"Error superseding memory: {e}"

            thenvoi_supersede_memory.__doc__ = get_tool_description(
                "thenvoi_supersede_memory"
            )
            agent.tool(thenvoi_supersede_memory)

            async def thenvoi_archive_memory(
                ctx: RunContext[AgentToolsProtocol],
                memory_id: str,
            ) -> dict[str, Any] | str:
                try:
                    return await ctx.deps.archive_memory(memory_id)
                except Exception as e:
                    return f"Error archiving memory: {e}"

            thenvoi_archive_memory.__doc__ = get_tool_description(
                "thenvoi_archive_memory"
            )
            agent.tool(thenvoi_archive_memory)

        # Register custom tools (user-provided PydanticAI-compatible functions)
        for custom_tool in self._custom_tools:
            agent.tool(custom_tool)
            logger.debug("Registered custom tool: %s", custom_tool.__name__)

        return agent

    # --- Adapted from ThenvoiPydanticAgent._handle_message ---
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: PydanticAIMessages,  # Already converted by SimpleAdapter
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle incoming platform message."""
        if self._agent is None:
            # Safety: create agent if not yet created (should be done in on_started)
            self._agent = self._create_agent()

        # Initialize message history for this room on first message
        # Note: history is already converted by SimpleAdapter via history_converter
        if is_session_bootstrap:
            if history:
                self._message_history[room_id] = list(history)
                logger.debug(
                    "Room %s: Loaded %s Pydantic AI messages", room_id, len(history)
                )
            else:
                self._message_history[room_id] = []
        elif room_id not in self._message_history:
            # Safety: ensure history exists even if not first message
            self._message_history[room_id] = []

        # Inject participants message if changed
        if participants_msg:
            self._message_history[room_id].append(
                ModelRequest(
                    parts=[UserPromptPart(content=f"[System]: {participants_msg}")]
                )
            )
            logger.debug("Room %s: Injected participant update into history", room_id)

        # Inject contacts message if present
        if contacts_msg:
            self._message_history[room_id].append(
                ModelRequest(
                    parts=[UserPromptPart(content=f"[System]: {contacts_msg}")]
                )
            )
            logger.debug("Room %s: Injected contacts broadcast into history", room_id)

        # Build user message with sender prefix
        user_message = msg.format_for_llm()

        logger.debug(
            "Room %s: Running Pydantic AI agent (history: %s msgs, prompt: %s...)",
            room_id,
            len(self._message_history[room_id]),
            user_message[:80],
        )

        # Run agent with streaming to capture tool events
        async for event in self._agent.run_stream_events(
            user_message,
            deps=tools,
            message_history=self._message_history[room_id],
        ):
            if isinstance(event, FunctionToolCallEvent):
                if Emit.EXECUTION in self.features.emit:
                    try:
                        await tools.send_event(
                            content=json.dumps(
                                {
                                    "name": event.part.tool_name,
                                    "args": event.part.args,
                                    "tool_call_id": event.part.tool_call_id,
                                }
                            ),
                            message_type="tool_call",
                        )
                    except Exception as e:
                        logger.warning("Failed to send tool_call event: %s", e)
            elif isinstance(event, FunctionToolResultEvent):
                if Emit.EXECUTION in self.features.emit:
                    try:
                        await tools.send_event(
                            content=json.dumps(
                                {
                                    "name": event.result.tool_name,
                                    "output": str(event.result.content),
                                    "tool_call_id": event.tool_call_id,
                                }
                            ),
                            message_type="tool_result",
                        )
                    except Exception as e:
                        logger.warning("Failed to send tool_result event: %s", e)
            elif isinstance(event, AgentRunResultEvent):
                # Update stored history with all messages from this run
                self._message_history[room_id] = list(event.result.all_messages())

        logger.debug(
            "Room %s: Pydantic AI agent completed (history now has %s messages)",
            room_id,
            len(self._message_history[room_id]),
        )

    # --- Copied from ThenvoiPydanticAgent._cleanup_session ---
    async def on_cleanup(self, room_id: str) -> None:
        """Clean up message history when agent leaves a room."""
        if room_id in self._message_history:
            del self._message_history[room_id]
            logger.debug("Room %s: Cleaned up message history", room_id)
