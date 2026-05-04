"""Letta adapter using AsyncLetta SDK with MCP tool execution."""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Any, Literal

from thenvoi.converters.letta import LettaHistoryConverter, LettaSessionState
from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.runtime.prompts import render_system_prompt

logger = logging.getLogger(__name__)

# Platform tools whose execution should not be reported as tool_call/tool_result
# events — they already produce visible output (messages or events) on the platform.
_SILENT_REPORTING_TOOLS: frozenset[str] = frozenset(
    {
        "thenvoi_send_message",
        "thenvoi_send_event",
    }
)

# Letta-specific preamble prepended to the system prompt when writing to the
# agent's instruction block.  In practice, models routed through Letta
# consistently skip tool calls entirely — likely because Letta injects its
# own system prompt that conflicts with ours.  This aggressive enforcement
# partially mitigates the issue but does not fully resolve it.
_LETTA_TOOL_ENFORCEMENT = """\
## MANDATORY: You MUST use tools to communicate

You are connected to a multi-agent chat platform via MCP tools.
Your plain text responses (assistant_message) are NOT delivered to anyone.
The ONLY way to communicate is by calling the provided tools.

EVERY response MUST include at least one tool call. Specifically:
- To send a message: call `thenvoi_send_message(content, mentions)` — this is REQUIRED
- To share your thinking: call `thenvoi_send_event(content, message_type="thought")`
- NEVER respond with just plain text — it will be silently discarded

## WRONG (message is lost):
Just responding with plain text like this.

## CORRECT:
Call thenvoi_send_message(content="Your response", mentions=["@user-id"])

If you respond without calling `thenvoi_send_message`, the user sees NOTHING.

"""


@dataclass
class LettaAdapterConfig:
    """Configuration for the Letta adapter.

    Works with both Letta Cloud and self-hosted Letta.  For Letta Cloud
    (the default), provide an ``api_key`` and optionally set ``project``
    to scope to a specific project.  For self-hosted, set ``base_url``
    to your server (e.g. ``"http://localhost:8283"``) — no ``api_key``
    is required.

    Note: The ``mcp_server_url`` is called by the Letta server, not by
    this adapter.  When using Letta Cloud, the MCP server must be
    publicly reachable (e.g. via ngrok or a deployed endpoint).  For
    self-hosted Letta, ``localhost`` works when both services run on the
    same host or Docker network.
    """

    agent_id: str | None = None
    model: str | None = None
    api_key: str | None = None  # Required for Letta Cloud, optional for self-hosted
    base_url: str = "https://api.letta.com"
    custom_section: str = ""
    include_base_instructions: bool = True
    enable_execution_reporting: bool = False
    enable_task_events: bool = True
    enable_memory_tools: bool = False
    persona: str | None = None
    turn_timeout_s: float = 300.0
    memory_blocks: list[dict[str, str]] = field(default_factory=list)
    summary_max_length: int = 150

    # Letta Cloud project scoping (ignored for self-hosted)
    project: str | None = None

    # MCP server configuration for tool execution
    mcp_server_url: str = "http://localhost:8002/sse"
    mcp_server_name: str = "thenvoi"

    # Operating mode: per_room creates one Letta agent per room,
    # shared uses one agent with per-room Conversations for isolation.
    mode: Literal["per_room", "shared"] = "per_room"


@dataclass
class _RoomContext:
    """Per-room state for a Letta agent."""

    agent_id: str
    conversation_id: str | None = None
    last_interaction: datetime | None = None
    summary: str | None = None


class LettaAdapter(SimpleAdapter[LettaSessionState]):
    """
    Letta adapter using the Letta Python SDK (letta-client).

    Uses MCP tools for platform tool execution — the Letta server calls the
    thenvoi-mcp server directly, keeping the adapter out of the tool execution
    path.  Supports two modes:

    - **per_room** (default): Each room gets its own Letta agent with isolated
      memory.
    - **shared**: One Letta agent shared across all rooms, with per-room
      isolation via the Conversations API.

    Example (Letta Cloud):
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                api_key="your-letta-api-key",
                model="openai/gpt-4o",
                mcp_server_url="http://localhost:8002/sse",
            ),

    Example (self-hosted):
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url="http://localhost:8283",
                model="openai/gpt-4o",
                mcp_server_url="http://localhost:8002/sse",
            ),
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.TASK_EVENTS}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        config: LettaAdapterConfig | None = None,
        history_converter: LettaHistoryConverter | None = None,
        *,
        features: AdapterFeatures | None = None,
    ) -> None:
        self._config = config or LettaAdapterConfig()

        # Detect non-default legacy booleans (enable_task_events defaults to
        # True, so only enable_memory_tools and enable_execution_reporting
        # count as "legacy usage").
        _has_legacy_booleans = (
            self._config.enable_memory_tools or self._config.enable_execution_reporting
        )

        if _has_legacy_booleans and features is not None:
            raise ThenvoiConfigError(
                "Cannot pass both legacy boolean flags in LettaAdapterConfig "
                "(enable_memory_tools / enable_execution_reporting) "
                "and 'features'. "
                "Use features=AdapterFeatures(...) instead."
            )

        # Build features from config booleans when not explicitly provided.
        if features is None:
            if _has_legacy_booleans:
                warnings.warn(
                    "enable_memory_tools and enable_execution_reporting in "
                    "LettaAdapterConfig are deprecated. "
                    "Use features=AdapterFeatures(capabilities={Capability.MEMORY}, "
                    "emit={Emit.EXECUTION}) instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            caps: frozenset[Capability] = frozenset()
            emit: frozenset[Emit] = frozenset()
            if self._config.enable_memory_tools:
                caps = caps | frozenset({Capability.MEMORY})
            if self._config.enable_execution_reporting:
                emit = emit | frozenset({Emit.EXECUTION})
            if self._config.enable_task_events:
                emit = emit | frozenset({Emit.TASK_EVENTS})
            features = AdapterFeatures(capabilities=caps, emit=emit)

        super().__init__(
            history_converter=history_converter or LettaHistoryConverter(),
            features=features,
        )
        self.config = self._config

        # Letta SDK async client (shared across rooms)
        self._client: Any = None

        # Per-room state
        self._rooms: dict[str, _RoomContext] = {}

        # Shared mode: single agent ID used across all rooms
        self._shared_agent_id: str | None = None

        # MCP server ID and tool IDs (populated in on_started)
        self._mcp_server_id: str | None = None
        self._mcp_tool_ids: list[str] = []

        # Protects agent creation only — not held during message handling,
        # so concurrent rooms can process messages in parallel.
        self._rpc_lock = asyncio.Lock()

        # Built during on_started
        self._system_prompt: str = ""

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Build system prompt, create Letta SDK client, register MCP server."""
        await super().on_started(agent_name, agent_description)

        self._system_prompt = render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.config.custom_section,
            include_base_instructions=self.config.include_base_instructions,
            features=self.features,
        )

        try:
            from letta_client import AsyncLetta  # type: ignore[import-not-found]  # optional dependency
        except ImportError:
            raise ImportError(
                "letta-client is required for LettaAdapter. "
                "Install with: pip install thenvoi-sdk[letta]"
            )

        client_kwargs: dict[str, Any] = {
            "base_url": self.config.base_url,
        }
        if self.config.api_key:
            client_kwargs["api_key"] = self.config.api_key
        if self.config.project:
            client_kwargs["project"] = self.config.project
        self._client = AsyncLetta(**client_kwargs)

        # Register MCP server with Letta
        await self._register_mcp_server()

        logger.info(
            "Letta adapter started for agent: %s (mode=%s)",
            agent_name,
            self.config.mode,
        )

    async def _register_mcp_server(self) -> None:
        """Register the thenvoi-mcp server with Letta and discover available tools.

        Uses lookup-or-create to handle adapter restarts where the MCP server
        name is already registered in Letta.
        """
        try:
            # Check if the MCP server is already registered
            servers = await self._client.mcp_servers.list()
            existing = next(
                (
                    s
                    for s in servers
                    if getattr(s, "server_name", None) == self.config.mcp_server_name
                    or getattr(s, "name", None) == self.config.mcp_server_name
                ),
                None,
            )

            if existing:
                server = existing
                logger.info(
                    "Found existing MCP server %r (id=%s)",
                    self.config.mcp_server_name,
                    server.id,
                )
            else:
                server = await self._client.mcp_servers.create(
                    server_name=self.config.mcp_server_name,
                    config={
                        "mcp_server_type": "sse",
                        "server_url": self.config.mcp_server_url,
                    },
                )
                logger.info(
                    "Registered MCP server %r (id=%s) at %s",
                    self.config.mcp_server_name,
                    server.id,
                    self.config.mcp_server_url,
                )

            self._mcp_server_id = server.id

            # Discover available tools from the MCP server
            tools = await self._client.mcp_servers.tools.list(
                mcp_server_id=server.id,
            )
            self._mcp_tool_ids = [t.id for t in tools if getattr(t, "id", None)]
            tool_names = [t.name for t in tools]
            logger.info(
                "Discovered %d MCP tools: %s",
                len(self._mcp_tool_ids),
                tool_names,
            )
        except Exception as e:
            logger.error("Failed to register MCP server: %s", e)
            raise RuntimeError(
                f"MCP server registration failed. Ensure the thenvoi-mcp server "
                f"is running at {self.config.mcp_server_url}: {e}"
            ) from e

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: LettaSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle incoming message via Letta API with MCP tools."""
        if not self._client:
            logger.error("Letta client not initialized, dropping message %s", msg.id)
            await self._report_error(tools, "Letta adapter not initialized")
            return

        # Lock only protects agent creation, not the full message path.
        # This allows concurrent rooms to process messages in parallel.
        if room_id not in self._rooms:
            async with self._rpc_lock:
                # Double-check after acquiring lock
                if room_id not in self._rooms:
                    await self._ensure_agent(room_id, history, tools)

        await self._handle_message(
            msg=msg,
            tools=tools,
            history=history,
            participants_msg=participants_msg,
            contacts_msg=contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )

    async def _handle_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: LettaSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Inner message handler."""
        # Agent must exist — ensured by on_message before calling this method.
        room_ctx = self._rooms.get(room_id)
        agent_id = (
            room_ctx.agent_id
            if room_ctx
            else await self._ensure_agent(room_id, history, tools)
        )

        # Build user message content
        # NOTE: Unlike other adapters that pass participants_msg and contacts_msg
        # as separate system messages, Letta uses a single-message API where each
        # call sends one user message.  We inject system context as [System]:
        # prefixed lines in the user message body so the agent sees participant
        # and contact updates inline with the conversation.
        parts: list[str] = []

        # Inject rejoin context when resuming after absence
        if is_session_bootstrap and room_ctx and room_ctx.last_interaction:
            time_ago = self._format_time_ago(room_ctx.last_interaction)
            rejoin_msg = f"[System]: You have rejoined this room after {time_ago}."
            if room_ctx.summary:
                rejoin_msg += f" Previous topic: {room_ctx.summary}"
            parts.append(rejoin_msg)

        # Inject participants update
        if participants_msg:
            parts.append(f"[System]: {participants_msg}")

        # Inject contacts update
        if contacts_msg:
            parts.append(f"[System]: {contacts_msg}")

        # Add the actual message
        user_message = msg.format_for_llm()
        parts.append(user_message)

        content = "\n\n".join(parts)

        logger.info(
            "Room %s: Sending message to Letta agent %s",
            room_id,
            agent_id,
        )

        # Send message and observe tool events
        try:
            final_text_parts = await asyncio.wait_for(
                self._send_message(
                    agent_id=agent_id,
                    content=content,
                    tools=tools,
                    room_id=room_id,
                    reply_to_sender_id=msg.sender_id,
                ),
                timeout=self.config.turn_timeout_s,
            )

            # Update room context after successful turn
            room_ctx = self._rooms.get(room_id)
            if room_ctx:
                room_ctx.last_interaction = datetime.now(timezone.utc)
                if final_text_parts:
                    room_ctx.summary = self._extract_summary(
                        final_text_parts, self.config.summary_max_length
                    )
        except asyncio.TimeoutError:
            logger.error(
                "Room %s: Letta turn timed out after %ss",
                room_id,
                self.config.turn_timeout_s,
            )
            await self._report_error(
                tools,
                f"Letta agent response timed out after {self.config.turn_timeout_s}s",
            )
        except Exception as e:
            logger.exception("Room %s: Error during Letta turn: %s", room_id, e)
            await self._report_error(tools, str(e))

    async def _send_message(
        self,
        agent_id: str,
        content: str,
        tools: AgentToolsProtocol,
        room_id: str,
        reply_to_sender_id: str = "",
    ) -> list[str]:
        """Send message to Letta and observe tool execution events.

        With MCP tools, the Letta server calls the MCP server directly.
        The adapter only observes tool_call_message / tool_return_message
        events in the response for execution reporting and auto-relay detection.

        Returns the list of assistant text parts collected during the turn.
        """
        messages = [{"role": "user", "content": content}]
        final_text_parts: list[str] = []
        used_send_message = False  # tracks if agent called thenvoi_send_message

        room_ctx = self._rooms.get(room_id)

        # Use Conversations API in shared mode, direct agent API in per_room mode
        if self.config.mode == "shared" and room_ctx and room_ctx.conversation_id:
            conversation_stream = await self._client.conversations.messages.create(
                conversation_id=room_ctx.conversation_id,
                messages=messages,
            )
            response_messages = [resp_msg async for resp_msg in conversation_stream]
        else:
            response = await self._client.agents.messages.create(
                agent_id=agent_id,
                messages=messages,
            )
            response_messages = list(response.messages)

        for resp_msg in response_messages:
            msg_type = getattr(resp_msg, "message_type", None)
            logger.debug("Room %s: Letta response message type=%s", room_id, msg_type)

            if msg_type == "assistant_message":
                text = getattr(resp_msg, "content", "") or ""
                logger.debug(
                    "Room %s: assistant_message content=%r", room_id, text[:200]
                )
                if text:
                    final_text_parts.append(text)

            elif msg_type == "tool_call_message":
                # MCP tool call executed server-side — observe for reporting
                tool_call = getattr(resp_msg, "tool_call", None)
                tool_name = (
                    getattr(tool_call, "name", "unknown") if tool_call else "unknown"
                )

                if tool_name == "thenvoi_send_message":
                    used_send_message = True

                if Emit.EXECUTION in self.features.emit:
                    if tool_name not in _SILENT_REPORTING_TOOLS:
                        await tools.send_event(
                            content=json.dumps(
                                {
                                    "name": tool_name,
                                    "args": getattr(tool_call, "arguments", "{}")
                                    if tool_call
                                    else "{}",
                                }
                            ),
                            message_type="tool_call",
                        )

            elif msg_type == "tool_return_message":
                # MCP tool result — observe for reporting
                if Emit.EXECUTION in self.features.emit:
                    tool_name = getattr(resp_msg, "tool_name", "unknown")
                    if tool_name not in _SILENT_REPORTING_TOOLS:
                        await tools.send_event(
                            content=json.dumps(
                                {
                                    "name": tool_name,
                                    "output": getattr(resp_msg, "tool_return", ""),
                                }
                            ),
                            message_type="tool_result",
                        )

        # If the agent already called thenvoi_send_message via MCP tools,
        # the message is on the platform — no auto-relay needed.  Otherwise
        # fall back to relaying the assistant_message text so the user still
        # sees a response.
        if used_send_message:
            logger.debug(
                "Room %s: Agent used thenvoi_send_message, skipping auto-relay",
                room_id,
            )
        elif final_text_parts:
            final_text = "\n\n".join(final_text_parts)
            mentions = [reply_to_sender_id] if reply_to_sender_id else None
            logger.info(
                "Room %s: Auto-relaying assistant text "
                "(agent did not use send_message)",
                room_id,
            )
            await tools.send_message(final_text, mentions=mentions)
        else:
            logger.debug("Room %s: Letta turn complete, no output", room_id)

        return final_text_parts

    async def _ensure_agent(
        self,
        room_id: str,
        history: LettaSessionState,
        tools: AgentToolsProtocol,
    ) -> str:
        """Ensure a Letta agent exists for this room, creating or resuming."""
        # Already have an agent for this room
        if room_id in self._rooms:
            return self._rooms[room_id].agent_id

        if self.config.mode == "shared":
            return await self._ensure_shared_agent(room_id, history, tools)
        return await self._ensure_per_room_agent(room_id, history, tools)

    async def _ensure_shared_agent(
        self,
        room_id: str,
        history: LettaSessionState,
        tools: AgentToolsProtocol,
    ) -> str:
        """Ensure a shared agent and per-room conversation exist."""
        # Create or resume the shared agent (once)
        if not self._shared_agent_id:
            resume_agent_id = (
                history.agent_id if history.has_agent() else self.config.agent_id
            )
            if resume_agent_id:
                try:
                    await self._client.agents.retrieve(resume_agent_id)
                    self._shared_agent_id = resume_agent_id
                    await self._update_instruction_block(resume_agent_id, room_id)
                    await self._verify_mcp_tools_attached(resume_agent_id)
                    logger.info("Shared mode: Resumed agent %s", resume_agent_id)
                except Exception as e:
                    logger.warning(
                        "Failed to resume shared agent %s: %s", resume_agent_id, e
                    )

            if not self._shared_agent_id:
                self._shared_agent_id = await self._create_agent()
                logger.info("Shared mode: Created agent %s", self._shared_agent_id)

        # Create a conversation for this room
        conversation = await self._client.conversations.create(
            agent_id=self._shared_agent_id,
        )
        conversation_id = conversation.id

        self._rooms[room_id] = _RoomContext(
            agent_id=self._shared_agent_id,
            conversation_id=conversation_id,
        )
        logger.info(
            "Room %s: Created conversation %s for shared agent %s",
            room_id,
            conversation_id,
            self._shared_agent_id,
        )

        await self._emit_task_event(
            tools, room_id, self._shared_agent_id, conversation_id
        )
        return self._shared_agent_id

    async def _ensure_per_room_agent(
        self,
        room_id: str,
        history: LettaSessionState,
        tools: AgentToolsProtocol,
    ) -> str:
        """Ensure a per-room Letta agent exists."""
        # Try to resume: prefer history agent_id, fall back to config agent_id
        resume_agent_id = (
            history.agent_id if history.has_agent() else self.config.agent_id
        )
        if resume_agent_id:
            try:
                await self._client.agents.retrieve(resume_agent_id)
                self._rooms[room_id] = _RoomContext(
                    agent_id=resume_agent_id,
                    conversation_id=history.conversation_id or None,
                )

                # Update instruction block and verify MCP tools
                await self._update_instruction_block(resume_agent_id, room_id)
                await self._verify_mcp_tools_attached(resume_agent_id)

                logger.info("Room %s: Resumed Letta agent %s", room_id, resume_agent_id)
                await self._emit_task_event(tools, room_id, resume_agent_id)
                return resume_agent_id
            except Exception as e:
                logger.warning(
                    "Room %s: Failed to resume agent %s: %s",
                    room_id,
                    resume_agent_id,
                    e,
                )

        # Create new agent
        agent_id = await self._create_agent()

        self._rooms[room_id] = _RoomContext(agent_id=agent_id)
        logger.info("Room %s: Created Letta agent %s", room_id, agent_id)

        await self._emit_task_event(tools, room_id, agent_id)
        return agent_id

    async def _create_agent(self) -> str:
        """Create a new Letta agent with MCP tools attached."""
        memory_blocks = (
            list(self.config.memory_blocks) if self.config.memory_blocks else []
        )

        # Add persona block with system prompt + tool enforcement
        base_prompt = self.config.persona or self._system_prompt
        persona_value = _LETTA_TOOL_ENFORCEMENT + base_prompt
        memory_blocks.insert(0, {"label": "persona", "value": persona_value})

        create_kwargs: dict[str, Any] = {
            "memory_blocks": memory_blocks,
            "include_base_tools": True,
        }
        if self.config.model:
            create_kwargs["model"] = self.config.model

        agent = await self._client.agents.create(**create_kwargs)
        agent_id = agent.id

        # Attach MCP tools to the agent
        await self._attach_mcp_tools(agent_id)

        return agent_id

    async def _attach_mcp_tools(self, agent_id: str) -> None:
        """Attach all discovered MCP tools to a Letta agent."""
        for tool_id in self._mcp_tool_ids:
            try:
                await self._client.agents.tools.attach(
                    agent_id=agent_id,
                    tool_id=tool_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to attach MCP tool %s to agent %s: %s",
                    tool_id,
                    agent_id,
                    e,
                )
        logger.debug(
            "Attached %d MCP tools to agent %s",
            len(self._mcp_tool_ids),
            agent_id,
        )

    async def _verify_mcp_tools_attached(self, agent_id: str) -> None:
        """Verify MCP tools are attached to an existing agent, re-attach if needed."""
        try:
            agent_tools_result = await self._client.agents.tools.list(agent_id=agent_id)
            if isinstance(agent_tools_result, list):
                agent_tools = agent_tools_result
            elif hasattr(agent_tools_result, "items"):
                agent_tools = list(agent_tools_result.items)
            else:
                agent_tools = [t async for t in agent_tools_result]

            attached_ids = {t.id for t in agent_tools if getattr(t, "id", None)}
            missing = [tid for tid in self._mcp_tool_ids if tid not in attached_ids]
            if missing:
                logger.info(
                    "Agent %s missing %d MCP tools, re-attaching",
                    agent_id,
                    len(missing),
                )
                for tool_id in missing:
                    try:
                        await self._client.agents.tools.attach(
                            agent_id=agent_id,
                            tool_id=tool_id,
                        )
                    except Exception as e:
                        logger.warning("Failed to re-attach tool %s: %s", tool_id, e)
        except Exception as e:
            logger.warning("Failed to verify MCP tools for agent %s: %s", agent_id, e)

    # Labels tried (in order) when injecting the system prompt into a
    # pre-existing agent's memory.  "persona" is the Letta default; others
    # are common alternatives created by Letta Cloud templates.
    _INSTRUCTION_BLOCK_LABELS: tuple[str, ...] = (
        "persona",
        "custom_instructions",
        "system_instructions",
    )

    async def _update_instruction_block(self, agent_id: str, room_id: str) -> None:
        """Update (or create) a memory block with the Thenvoi system prompt."""
        base = self.config.persona or self._system_prompt
        value = _LETTA_TOOL_ENFORCEMENT + base

        # Try known instruction-block labels in priority order
        for label in self._INSTRUCTION_BLOCK_LABELS:
            try:
                await self._client.agents.blocks.update(
                    label,
                    agent_id=agent_id,
                    value=value,
                )
                logger.debug(
                    "Room %s: Updated %r block for agent %s",
                    room_id,
                    label,
                    agent_id,
                )
                return
            except Exception:
                # Label not found on this agent, try next
                logger.debug(
                    "Room %s: Block %r not found for agent %s, trying next",
                    room_id,
                    label,
                    agent_id,
                )
                continue

        # None of the known labels exist — create a "persona" block
        try:
            block = await self._client.blocks.create(
                label="persona",
                value=value,
            )
            await self._client.agents.blocks.attach(
                block.id,
                agent_id=agent_id,
            )
            logger.debug(
                "Room %s: Created and attached persona block for agent %s",
                room_id,
                agent_id,
            )
        except Exception as e:
            logger.warning(
                "Room %s: Could not update or create instruction block: %s",
                room_id,
                e,
            )

    async def _emit_task_event(
        self,
        tools: AgentToolsProtocol,
        room_id: str,
        agent_id: str,
        conversation_id: str | None = None,
    ) -> None:
        """Emit a task event with agent/room mapping metadata."""
        if Emit.TASK_EVENTS not in self.features.emit:
            return
        try:
            if conversation_id is None:
                room_ctx = self._rooms.get(room_id)
                conversation_id = room_ctx.conversation_id if room_ctx else None
            metadata: dict[str, Any] = {
                "letta_agent_id": agent_id,
                "letta_room_id": room_id,
                "letta_created_at": datetime.now(timezone.utc).isoformat(),
            }
            if conversation_id:
                metadata["letta_conversation_id"] = conversation_id
            await tools.send_event(
                content=f"Letta agent {agent_id} active for room {room_id}",
                message_type="task",
                metadata=metadata,
            )
        except Exception as e:
            logger.warning("Failed to emit task event: %s", e)

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up per-room state. Does NOT delete the Letta agent."""
        async with self._rpc_lock:
            room_ctx = self._rooms.get(room_id)
            if room_ctx and self._client:
                await self._consolidate_memory(room_ctx.agent_id, room_id)
            self._rooms.pop(room_id, None)
        logger.debug("Room %s: Cleaned up Letta adapter state", room_id)

    async def _consolidate_memory(self, agent_id: str, room_id: str) -> None:
        """Send a consolidation prompt so the agent saves key context to memory.

        Best-effort: failures are logged but do not propagate.
        """
        try:
            await self._client.agents.messages.create(
                agent_id=agent_id,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "[System]: You are leaving this room. Consolidate key "
                            "decisions, action items, and important context into "
                            "your memory now."
                        ),
                    }
                ],
            )
            logger.debug(
                "Room %s: Sent memory consolidation prompt to agent %s",
                room_id,
                agent_id,
            )
        except Exception as e:
            logger.warning("Room %s: Memory consolidation failed: %s", room_id, e)

    @staticmethod
    def _format_time_ago(dt: datetime) -> str:
        """Format a datetime as a human-readable time-ago string."""
        now = datetime.now(timezone.utc)
        # Ensure dt is timezone-aware for comparison
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s"
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h" if hours > 1 else "1 hour"
        days = hours // 24
        return f"{days}d" if days > 1 else "1 day"

    @staticmethod
    def _extract_summary(parts: list[str], max_length: int = 150) -> str:
        """Extract a brief summary from assistant text parts.

        Uses the first sentence if available, otherwise truncates to max_length.
        """
        text = " ".join(parts).strip()
        if not text:
            return ""

        # Find the earliest sentence-ending delimiter
        earliest_idx = -1
        for delimiter in (".", "!", "?"):
            idx = text.find(delimiter)
            if idx != -1 and (earliest_idx == -1 or idx < earliest_idx):
                earliest_idx = idx
        if earliest_idx != -1:
            sentence = text[: earliest_idx + 1].strip()
            if len(sentence) <= max_length:
                return sentence

        # No sentence delimiter found or sentence too long — truncate
        if len(text) <= max_length:
            return text
        return text[:max_length].rsplit(" ", 1)[0] + "..."

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception:
            logger.debug("Failed to report error to platform: %s", error)
