"""Letta adapter using AsyncLetta SDK with MCP tool execution."""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Any

from band.converters.letta import LettaHistoryConverter, LettaSessionState
from band.core.exceptions import BandConfigError
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
    TurnUsage,
)
from band.integrations.letta.config import (
    LettaAdapterConfig,
    LettaMCPConfig,
    MCPTransport,
)
from band.integrations.letta.mcp import LettaMCPBridge, bounded_teardown
from band.integrations.letta.prompts import render_tool_enforcement
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import iter_tool_definitions

__all__ = [
    "LettaAdapter",
    "LettaAdapterConfig",
    "LettaMCPConfig",
    "MCPTransport",
]

logger = logging.getLogger(__name__)


@dataclass
class _RoomContext:
    """Per-room state for a Letta agent."""

    agent_id: str
    conversation_id: str | None = None
    last_interaction: datetime | None = None
    summary: str | None = None
    # The room's current platform tools, refreshed each turn; the self-hosted
    # MCP server resolves tool calls through this.
    tools: AgentToolsProtocol | None = None
    # Replay lines from platform history awaiting injection into the next
    # message — set when a fresh Letta agent is created for a room that
    # already has history (cold boot), cleared once delivered.
    pending_seed: list[str] = field(default_factory=list)
    # Set when the MCP registration is released while this room stays in
    # memory (adapter stop): the next registration carries new tool ids, so
    # the room's agent must re-verify attachment before its next turn.
    stale_tools: bool = False


class LettaAdapter(SimpleAdapter[LettaSessionState]):
    """
    Letta adapter using the Letta Python SDK (letta-client).

    Uses MCP tools for platform tool execution — the Letta server calls a Band
    MCP server directly, keeping the adapter out of the tool execution path.
    By default the adapter self-hosts that MCP server in-process (see
    ``LettaMCPConfig``); pointing ``mcp`` at an external band-mcp is the
    Letta Cloud topology.  Supports two modes:

    - **per_room** (default): Each room gets its own Letta agent with isolated
      memory.
    - **shared**: One Letta agent shared across all rooms, with per-room
      isolation via the Conversations API.

    Example (self-hosted Letta, self-hosted MCP — the default):
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                base_url="http://localhost:8283",
                model="openai/gpt-5.4",
            ),
        )

    Example (Letta Cloud + external band-mcp):
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                provider_key="your-letta-api-key",
                model="openai/gpt-5.4",
                mcp=LettaMCPConfig(
                    mode="external",
                    server_url="https://your-band-mcp.example.com/sse",
                ),
            ),
        )
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.TASK_EVENTS, Emit.USAGE}
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
            raise BandConfigError(
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

        # The Band MCP tool path: self-hosted server + Letta registration.
        self._mcp = LettaMCPBridge(
            self.config.mcp,
            tool_definitions=iter_tool_definitions(
                include_memory=Capability.MEMORY in self.features.capabilities,
                include_contacts=Capability.CONTACTS in self.features.capabilities,
            ),
            get_tools=self._get_room_tools,
            teardown_timeout_s=self.config.teardown_timeout_s,
        )

        # Protects agent creation and MCP (re)registration only — not held
        # during message handling, so concurrent rooms process in parallel.
        self._rpc_lock = asyncio.Lock()

        # Built during on_started
        self._system_prompt: str = ""

    def _get_room_tools(self, room_id: str) -> AgentToolsProtocol | None:
        """Resolve room-scoped tools for the self-hosted MCP server."""
        room_ctx = self._rooms.get(room_id)
        return room_ctx.tools if room_ctx else None

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Build system prompt, create Letta SDK client, wire the MCP tool path."""
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
                "Install with: pip install band-sdk[letta]"
            )

        client_kwargs: dict[str, Any] = {
            "base_url": self.config.base_url,
        }
        if self.config.provider_key:
            client_kwargs["api_key"] = self.config.provider_key
        if self.config.project:
            client_kwargs["project"] = self.config.project
        self._client = AsyncLetta(**client_kwargs)

        # Fail loud at startup if the tool path cannot be wired — the adapter
        # is useless without it.
        await self._mcp.ensure_ready(self._client)

        logger.info(
            "Letta adapter started for agent: %s (mode=%s, mcp=%s)",
            agent_name,
            self.config.mode,
            self.config.mcp.mode,
        )

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

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

        # Lock only protects MCP/agent setup, not the full message path.
        # This allows concurrent rooms to process messages in parallel. Both
        # ensures are idempotent; the outer check just avoids the lock on the
        # established-room fast path. The MCP path may need re-registering
        # after cleanup_all released it — retained rooms then carry
        # stale_tools so their agents re-sync against the new registration.
        try:
            room_ctx = self._rooms.get(room_id)
            if not self._mcp.ready or room_ctx is None or room_ctx.stale_tools:
                async with self._rpc_lock:
                    await self._mcp.ensure_ready(self._client)
                    await self._ensure_agent(room_id, history, tools)
        except Exception as e:
            logger.exception("Room %s: Failed to prepare Letta session: %s", room_id, e)
            await self._report_error(tools, str(e))
            return

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
        """Run one Letta turn: resolve the room, compose the message, send."""
        if (room_ctx := await self._room_context(room_id, history, tools)) is None:
            logger.error("Room %s: No Letta agent context, dropping message", room_id)
            await self._report_error(tools, "Letta agent context unavailable")
            return

        # Point the MCP resolver at this room's current tools for the
        # server-side tool calls this turn will make.
        room_ctx.tools = tools

        content = self._compose_turn_content(
            msg,
            room_ctx,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )
        await self._run_turn(msg, tools, room_ctx, content, room_id)

    async def _room_context(
        self,
        room_id: str,
        history: LettaSessionState,
        tools: AgentToolsProtocol,
    ) -> _RoomContext | None:
        """The room's context — normally pre-created by ``on_message``; falls
        back to creating the agent when a racing cleanup popped the room."""
        if (room_ctx := self._rooms.get(room_id)) is not None:
            return room_ctx
        async with self._rpc_lock:
            if (room_ctx := self._rooms.get(room_id)) is not None:
                return room_ctx
            await self._ensure_agent(room_id, history, tools)
        return self._rooms.get(room_id)

    def _compose_turn_content(
        self,
        msg: PlatformMessage,
        room_ctx: _RoomContext,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> str:
        """Assemble the turn's single user-message body.

        Letta's API takes one user message per call, so system context rides
        inline as ``[System]:`` lines: the one-shot cold-boot history seed, a
        rejoin note after absence, the shared-mode room-id hint (a shared
        instruction block cannot carry a per-room id), participants/contacts
        updates, then the triggering message itself.
        """
        parts: list[str] = []

        if seed := room_ctx.pending_seed:
            parts.append(
                "[System]: You are joining an ongoing conversation. Earlier "
                "messages in this room, oldest first (prior context only — "
                "do not answer them again):\n" + "\n".join(seed)
            )

        if is_session_bootstrap and room_ctx.last_interaction:
            rejoin = (
                "[System]: You have rejoined this room after "
                f"{self._format_time_ago(room_ctx.last_interaction)}."
            )
            if room_ctx.summary:
                rejoin += f" Previous topic: {room_ctx.summary}"
            parts.append(rejoin)

        if self.config.mcp.mode == "self_host" and self.config.mode == "shared":
            parts.append(
                f"[System]: Current room_id: {room_id} — pass it as the "
                "`room_id` argument in every tool call."
            )

        if participants_msg:
            parts.append(f"[System]: {participants_msg}")
        if contacts_msg:
            parts.append(f"[System]: {contacts_msg}")

        parts.append(msg.format_for_llm())
        return "\n\n".join(parts)

    async def _run_turn(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        room_ctx: _RoomContext,
        content: str,
        room_id: str,
    ) -> None:
        """Send one turn to Letta and record its outcome on the room context."""
        logger.info(
            "Room %s: Sending message to Letta agent %s", room_id, room_ctx.agent_id
        )
        try:
            final_text_parts = await asyncio.wait_for(
                self._send_message(
                    agent_id=room_ctx.agent_id,
                    content=content,
                    tools=tools,
                    room_ctx=room_ctx,
                    room_id=room_id,
                    reply_to_sender_id=msg.sender_id,
                ),
                timeout=self.config.turn_timeout_s,
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
        else:
            if room_ctx.pending_seed:
                room_ctx.pending_seed = []
            room_ctx.last_interaction = datetime.now(timezone.utc)
            if final_text_parts:
                room_ctx.summary = self._extract_summary(
                    final_text_parts, self.config.summary_max_length
                )

    async def _send_message(
        self,
        agent_id: str,
        content: str,
        tools: AgentToolsProtocol,
        room_ctx: _RoomContext,
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
        # Per-turn token usage. Reachable only on the per_room path, where the
        # non-streamed LettaResponse carries an aggregate .usage; the shared-mode
        # Conversations stream exposes no such aggregate, so it stays empty (N-A).
        # Emitted on every exit via the finally.
        turn_usage = TurnUsage()

        try:
            # Use Conversations API in shared mode, direct agent API in per_room mode
            if self.config.mode == "shared" and room_ctx.conversation_id:
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
                turn_usage = self._usage_from_response(response)

            return await self._process_response_messages(
                response_messages,
                tools,
                room_id,
                reply_to_sender_id,
            )
        finally:
            # No-op unless Emit.USAGE is on; best-effort, never raises.
            await self.emit_usage(tools, turn_usage)

    async def _process_response_messages(
        self,
        response_messages: list[Any],
        tools: AgentToolsProtocol,
        room_id: str,
        reply_to_sender_id: str,
    ) -> list[str]:
        """Observe Letta response messages: report tool events, auto-relay text.

        Returns the list of assistant text parts collected during the turn.
        """
        final_text_parts: list[str] = []
        used_send_message = False  # tracks if agent called the MCP send tool
        for resp_msg in response_messages:
            match getattr(resp_msg, "message_type", None):
                case "assistant_message":
                    if text := (getattr(resp_msg, "content", "") or ""):
                        final_text_parts.append(text)
                case "tool_call_message":
                    # MCP tool call executed server-side — observe only
                    tool_call = getattr(resp_msg, "tool_call", None)
                    tool_name = (
                        getattr(tool_call, "name", "unknown")
                        if tool_call
                        else "unknown"
                    )
                    if tool_name == self._mcp.send_message_tool:
                        used_send_message = True
                    await self._report_execution_event(
                        tools,
                        "tool_call",
                        tool_name,
                        {
                            "name": tool_name,
                            "args": getattr(tool_call, "arguments", "{}")
                            if tool_call
                            else "{}",
                        },
                    )
                case "tool_return_message":
                    tool_name = getattr(resp_msg, "tool_name", "unknown")
                    await self._report_execution_event(
                        tools,
                        "tool_result",
                        tool_name,
                        {
                            "name": tool_name,
                            "output": getattr(resp_msg, "tool_return", ""),
                        },
                    )

        # If the agent already sent via the MCP send tool, the message is on
        # the platform — nothing to relay.  Otherwise fall back to relaying the
        # assistant text so the user still sees a response — loudly, because a
        # turn landing here means the MCP tool path went unused (a dead tool
        # path would otherwise hide behind green relays).  With auto_relay
        # disabled, the unused tool path fails loud as an error event instead.
        if used_send_message:
            logger.debug(
                "Room %s: Agent used %s, skipping auto-relay",
                room_id,
                self._mcp.send_message_tool,
            )
        elif not final_text_parts:
            logger.debug("Room %s: Letta turn complete, no output", room_id)
        elif not self.config.auto_relay:
            logger.error(
                "Room %s: Agent did not call %s and auto-relay is disabled; "
                "dropping assistant text",
                room_id,
                self._mcp.send_message_tool,
            )
            await self._report_error(
                tools,
                f"Letta agent did not call {self._mcp.send_message_tool} "
                "(auto-relay disabled); its reply was dropped",
            )
        else:
            final_text = "\n\n".join(final_text_parts)
            mentions = [reply_to_sender_id] if reply_to_sender_id else None
            logger.warning(
                "Room %s: Agent did not call %s — auto-relaying assistant text",
                room_id,
                self._mcp.send_message_tool,
            )
            await tools.send_message(final_text, mentions=mentions)

        return final_text_parts

    async def _report_execution_event(
        self,
        tools: AgentToolsProtocol,
        event_type: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a tool_call/tool_result event when EXECUTION reporting is on.

        The send tools stay silent — their execution already produces visible
        platform output, so reporting them would be duplicate noise.
        """
        if Emit.EXECUTION not in self.features.emit:
            return
        if tool_name in self._mcp.silent_reporting_tools:
            return
        await tools.send_event(content=json.dumps(payload), message_type=event_type)

    # ------------------------------------------------------------------
    # Letta agent lifecycle
    # ------------------------------------------------------------------

    async def _ensure_agent(
        self,
        room_id: str,
        history: LettaSessionState,
        tools: AgentToolsProtocol,
    ) -> str:
        """Ensure a Letta agent exists for this room, creating or resuming."""
        # Already have an agent for this room; if the MCP registration
        # rotated since it was wired (release + re-register), its old tool
        # ids died with the old registration — re-verify attachment first.
        if (room_ctx := self._rooms.get(room_id)) is not None:
            if room_ctx.stale_tools:
                if await self._verify_mcp_tools_attached(room_ctx.agent_id):
                    room_ctx.stale_tools = False
                else:
                    raise RuntimeError(
                        "MCP tools are not attached to the Letta agent after "
                        "registration rotation"
                    )
            return room_ctx.agent_id

        if self.config.mode == "shared":
            return await self._ensure_shared_agent(room_id, history, tools)
        return await self._ensure_per_room_agent(room_id, history, tools)

    async def _resume_agent(self, agent_id: str, room_id: str) -> bool:
        """Resume an existing Letta agent: retrieve it, refresh its instruction
        block, and re-attach any missing MCP tools.  False when it cannot be
        resumed (deleted agent, unreachable server) — callers then create fresh.
        """
        try:
            await self._client.agents.retrieve(agent_id)
            await self._update_instruction_block(agent_id, room_id)
            await self._verify_mcp_tools_attached(agent_id)
            return True
        except Exception as e:
            logger.warning(
                "Room %s: Failed to resume agent %s: %s", room_id, agent_id, e
            )
            return False

    def _resume_candidate(self, history: LettaSessionState) -> str | None:
        """The agent id to try resuming.

        Per-room mode only resumes from persisted room history — never from
        ``config.agent_id``, which would converge unrelated rooms onto one
        Letta agent and clobber each other's persona blocks.  Shared mode also
        accepts ``config.agent_id`` as a bootstrap hint for the lone agent.
        """
        if history.has_agent():
            return history.agent_id
        if self.config.mode == "shared":
            return self.config.agent_id
        return None

    async def _ensure_shared_agent(
        self,
        room_id: str,
        history: LettaSessionState,
        tools: AgentToolsProtocol,
    ) -> str:
        """Ensure a shared agent and per-room conversation exist."""
        # Create or resume the shared agent (once)
        if not self._shared_agent_id:
            resume_agent_id = self._resume_candidate(history)
            if resume_agent_id and await self._resume_agent(resume_agent_id, room_id):
                self._shared_agent_id = resume_agent_id
                logger.info("Shared mode: Resumed agent %s", resume_agent_id)
            else:
                self._shared_agent_id = await self._create_agent()
                logger.info("Shared mode: Created agent %s", self._shared_agent_id)
        else:
            # The MCP registration (and its tool ids) may have rotated since
            # the agent was wired — e.g. a self-hosted server restart after a
            # full room cleanup — so re-verify attachment for each new room.
            await self._verify_mcp_tools_attached(self._shared_agent_id)

        # Resume the room's persisted conversation when one exists — a fresh
        # conversation would silently drop the room's conversational context.
        conversation_id = None
        if history.conversation_id:
            try:
                conversation = await self._client.conversations.retrieve(
                    history.conversation_id
                )
                conv_agent_id = getattr(conversation, "agent_id", None)
                if conv_agent_id and conv_agent_id != self._shared_agent_id:
                    logger.warning(
                        "Room %s: Conversation %s belongs to agent %s, not "
                        "shared agent %s — creating a fresh conversation",
                        room_id,
                        history.conversation_id,
                        conv_agent_id,
                        self._shared_agent_id,
                    )
                else:
                    conversation_id = history.conversation_id
                    logger.info(
                        "Room %s: Resumed conversation %s", room_id, conversation_id
                    )
            except Exception as e:
                logger.warning(
                    "Room %s: Failed to resume conversation %s: %s",
                    room_id,
                    history.conversation_id,
                    e,
                )

        room_ctx = _RoomContext(agent_id=self._shared_agent_id)
        if conversation_id is None:
            conversation = await self._client.conversations.create(
                agent_id=self._shared_agent_id,
            )
            conversation_id = conversation.id
            # A brand-new conversation for a room with history: seed it (the
            # shared agent's other conversations never saw this room's messages).
            if history.replay_messages:
                room_ctx.pending_seed = list(history.replay_messages)
            logger.info(
                "Room %s: Created conversation %s for shared agent %s",
                room_id,
                conversation_id,
                self._shared_agent_id,
            )
        room_ctx.conversation_id = conversation_id
        self._rooms[room_id] = room_ctx

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
        resume_agent_id = self._resume_candidate(history)
        if resume_agent_id and await self._resume_agent(resume_agent_id, room_id):
            self._rooms[room_id] = _RoomContext(
                agent_id=resume_agent_id,
                conversation_id=history.conversation_id or None,
            )
            logger.info("Room %s: Resumed Letta agent %s", room_id, resume_agent_id)
            await self._emit_task_event(tools, room_id, resume_agent_id)
            return resume_agent_id

        # Create new agent; seed it from platform history when the room
        # already has some (no live agent to resume — the cold-boot case).
        agent_id = await self._create_agent(room_id)

        room_ctx = _RoomContext(agent_id=agent_id)
        if history.replay_messages:
            room_ctx.pending_seed = list(history.replay_messages)
            logger.info(
                "Room %s: Seeding new Letta agent %s with %d history lines",
                room_id,
                agent_id,
                len(history.replay_messages),
            )
        self._rooms[room_id] = room_ctx
        logger.info("Room %s: Created Letta agent %s", room_id, agent_id)

        await self._emit_task_event(tools, room_id, agent_id)
        return agent_id

    def _instruction_text(self, room_id: str | None) -> str:
        """The instruction block content: tool enforcement + system prompt.

        The room id is rendered into the enforcement only when the tool
        schemas require it per call (self-hosted MCP) and the agent serves
        exactly one room (per_room mode) — shared agents get the room id as a
        per-message [System] line instead.
        """
        base = self.config.persona or self._system_prompt
        include_room = (
            room_id
            if self.config.mcp.mode == "self_host" and self.config.mode == "per_room"
            else None
        )
        return (
            render_tool_enforcement(
                self._mcp.send_message_tool,
                self._mcp.send_event_tool,
                room_id=include_room,
            )
            + base
        )

    async def _create_agent(self, room_id: str | None = None) -> str:
        """Create a new Letta agent with MCP tools attached."""
        memory_blocks = (
            list(self.config.memory_blocks) if self.config.memory_blocks else []
        )

        # Add persona block with tool enforcement + system prompt
        memory_blocks.insert(
            0, {"label": "persona", "value": self._instruction_text(room_id)}
        )

        create_kwargs: dict[str, Any] = {
            "memory_blocks": memory_blocks,
            "include_base_tools": True,
        }
        if self.config.model:
            create_kwargs["model"] = self.config.model
        # Letta's Docker server requires an embedding model on agent create.
        if self.config.embedding:
            create_kwargs["embedding"] = self.config.embedding

        agent = await self._client.agents.create(**create_kwargs)
        agent_id = agent.id

        # Attach MCP tools to the agent
        await self._attach_mcp_tools(agent_id)

        return agent_id

    async def _attach_mcp_tools(self, agent_id: str) -> None:
        """Attach all discovered MCP tools to a Letta agent."""
        for tool_id in self._mcp.tool_ids:
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
            len(self._mcp.tool_ids),
            agent_id,
        )

    async def _verify_mcp_tools_attached(self, agent_id: str) -> bool:
        """Verify MCP tools are attached to an existing agent, re-attach if needed.

        Best-effort: failures are logged, not raised.  Returns False when the
        agent may still be missing tools, so callers tracking staleness can
        retry on the next turn instead of considering the agent synced.
        """
        try:
            agent_tools_result = await self._client.agents.tools.list(agent_id=agent_id)
            if isinstance(agent_tools_result, list):
                agent_tools = agent_tools_result
            elif hasattr(agent_tools_result, "items"):
                agent_tools = list(agent_tools_result.items)
            else:
                agent_tools = [t async for t in agent_tools_result]

            attached_ids = {t.id for t in agent_tools if getattr(t, "id", None)}
            missing = [tid for tid in self._mcp.tool_ids if tid not in attached_ids]
            complete = True
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
                        complete = False
            return complete
        except Exception as e:
            logger.warning("Failed to verify MCP tools for agent %s: %s", agent_id, e)
            return False

    # Labels tried (in order) when injecting the system prompt into a
    # pre-existing agent's memory.  "persona" is the Letta default; others
    # are common alternatives created by Letta Cloud templates.
    _INSTRUCTION_BLOCK_LABELS: tuple[str, ...] = (
        "persona",
        "custom_instructions",
        "system_instructions",
    )

    async def _update_instruction_block(self, agent_id: str, room_id: str) -> None:
        """Update (or create) a memory block with the Band system prompt."""
        value = self._instruction_text(room_id)

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

    # ------------------------------------------------------------------
    # Cleanup / shutdown
    # ------------------------------------------------------------------

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up per-room state.

        Letta agents are kept by default so resume-by-id works across
        restarts; ``delete_agents_on_cleanup`` opts into deleting them
        (per_room mode).  The self-hosted MCP server and its registration are
        adapter-scoped, not room-scoped — they persist across room churn (see
        ``LettaMCPBridge.release`` for why the server is never stopped).  Only
        the state mutation holds the lock — the consolidation LLM turn must
        not block other rooms' setup.
        """
        async with self._rpc_lock:
            room_ctx = self._rooms.pop(room_id, None)

        if room_ctx and self._client:
            if self.config.delete_agents_on_cleanup and self.config.mode == "per_room":
                await self._delete_agent(room_ctx.agent_id, room_id)
            elif (
                self.config.consolidate_memory_on_cleanup
                and self.config.mode == "per_room"
            ):
                # Shared agents serve other rooms — an agent-level consolidation
                # turn would run against the wrong context and leak across rooms.
                await self._consolidate_memory(room_ctx.agent_id, room_id)
        logger.debug("Room %s: Cleaned up Letta adapter state", room_id)

    async def cleanup_all(self) -> None:
        """Release the MCP tool path on agent shutdown.

        See ``LettaMCPBridge.release`` for the three registration ownership
        tiers (external / fixed self-host / ephemeral self-host).  Retained
        rooms are marked ``stale_tools`` only when ``release`` deregisters
        from Letta — ephemeral self-host — because that rotation mints new
        tool ids that existing agents must re-attach.
        """
        if self._mcp.registration_rotates_on_release and self._mcp.server_id:
            for room_ctx in self._rooms.values():
                room_ctx.stale_tools = True
        await self._mcp.release(self._client)

    async def _delete_agent(self, agent_id: str, room_id: str) -> None:
        """Delete the room's Letta agent (opt-in hygiene). Best-effort."""
        ok = await bounded_teardown(
            self._client.agents.delete(agent_id),
            timeout_s=self.config.teardown_timeout_s,
            action=f"delete Letta agent {agent_id} for room {room_id}",
        )
        if ok:
            logger.info("Room %s: Deleted Letta agent %s", room_id, agent_id)

    async def _consolidate_memory(self, agent_id: str, room_id: str) -> None:
        """Send a consolidation prompt so the agent saves key context to memory.

        Best-effort: failures are logged but do not propagate.
        """
        ok = await bounded_teardown(
            self._client.agents.messages.create(
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
            ),
            timeout_s=self.config.teardown_timeout_s,
            action=f"consolidate memory for room {room_id}",
        )
        if ok:
            logger.debug(
                "Room %s: Sent memory consolidation prompt to agent %s",
                room_id,
                agent_id,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _usage_from_response(response: Any) -> TurnUsage:
        """Map a Letta ``LettaResponse.usage`` (a ``Usage``) onto TurnUsage.

        Field names verified against letta-client: ``prompt_tokens`` /
        ``completion_tokens`` / ``cached_input_tokens`` / ``cache_write_tokens``.
        A missing ``usage`` yields empty usage.
        """
        return TurnUsage.from_object(
            getattr(response, "usage", None),
            input="prompt_tokens",
            output="completion_tokens",
            cache_read="cached_input_tokens",
            cache_write="cache_write_tokens",
        )

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
        """A bounded prefix of the turn's text, used as the rejoin topic hint.

        Deliberately not sentence-aware: delimiter heuristics misfire on
        decimals, abbreviations, and code ("pi is 3.14" -> "pi is 3."), and an
        LLM-facing hint only needs a stable prefix.  Truncates on a word
        boundary with an ellipsis.
        """
        text = " ".join(parts).strip()
        if len(text) <= max_length:
            return text
        return text[:max_length].rsplit(" ", 1)[0] + "..."

    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception:
            logger.debug("Failed to report error to platform: %s", error)
