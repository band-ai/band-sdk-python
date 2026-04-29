"""
Claude SDK adapter using SimpleAdapter pattern.

Extracted from thenvoi.integrations.claude_sdk.agent.ThenvoiClaudeSDKAgent.

Note: This adapter is more complex than Anthropic/PydanticAI because Claude SDK
uses MCP servers which need access to tools by room_id. We store tools per-room
when on_message is called so the MCP server can access them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

try:
    from claude_agent_sdk import (  # type: ignore[import-not-found]
        ClaudeSDKClient,
        ClaudeAgentOptions,
        AssistantMessage,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        ToolResultBlock,
        ResultMessage,
    )
    from claude_agent_sdk._errors import CLIConnectionError  # type: ignore[import-not-found]
    from claude_agent_sdk.types import (  # type: ignore[import-not-found]
        CanUseTool,
        HookContext,
        HookInput,
        HookJSONOutput,
        HookMatcher,
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
    )

    _CLAUDE_SDK_AVAILABLE = True
except ImportError:
    _CLAUDE_SDK_AVAILABLE = False

from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.converters.claude_sdk import (
    ClaudeSDKHistoryConverter,
    ClaudeSDKSessionState,
)
from thenvoi.integrations.mcp.backends import (
    ThenvoiMCPBackend,
    create_thenvoi_mcp_backend,
)
from thenvoi.integrations.claude_sdk.session_manager import ClaudeSessionManager
from thenvoi.integrations.claude_sdk.prompts import generate_claude_sdk_agent_prompt
from thenvoi.runtime.custom_tools import CustomToolDef
from thenvoi.runtime.tools import (
    ALL_TOOL_NAMES,
    BASE_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    iter_tool_definitions,
    mcp_tool_names,
)

logger = logging.getLogger(__name__)


# Tool names as constants (MCP naming convention: mcp__{server}__{tool})
# Derived from TOOL_MODELS — single source of truth
THENVOI_BASE_TOOLS: list[str] = mcp_tool_names(BASE_TOOL_NAMES)
THENVOI_MEMORY_TOOLS: list[str] = mcp_tool_names(MEMORY_TOOL_NAMES)
# All tools: chat + contacts + memory (17 total). For chat-only tools (7),
# see thenvoi.integrations.claude_sdk.tools.THENVOI_CHAT_TOOLS.
THENVOI_ALL_TOOLS: list[str] = mcp_tool_names(ALL_TOOL_NAMES)

_THENVOI_TOOLS: list[str] = THENVOI_ALL_TOOLS

# Approval flow types (mirrors Codex adapter patterns)
ApprovalMode = Literal["auto_accept", "auto_decline", "manual"]
ApprovalDecision = Literal["accept", "decline"]

# Commands recognised as local (not forwarded to Claude)
_APPROVAL_CMDS = frozenset({"approve", "decline", "approvals"})
_LOCAL_CMDS = _APPROVAL_CMDS | frozenset({"status"})

# Patterns that look like secrets/tokens in shell commands
_REDACT_RE = re.compile(
    r"""(?x)
    (?:                             # key=value style
        (?:key|token|secret|password|passwd|pwd|auth|bearer|credential)
        \s*[=:]\s*
    )
    \S+                             # the secret value
    """,
    re.IGNORECASE,
)


async def _pre_tool_use_continue_hook(
    _hook_input: HookInput,
    _tool_name: str | None,
    _context: HookContext,
) -> HookJSONOutput:
    """PreToolUse hook that delegates every tool to ``can_use_tool``.

    Returning ``{"continue_": True}`` tells the SDK to skip its built-in
    permission resolution and call the ``can_use_tool`` callback instead.
    """
    return {"continue_": True}


@dataclass
class _PendingApproval:
    """A tool-use approval request waiting for a chat-room decision."""

    tool_name: str
    tool_input: dict[str, Any]
    summary: str
    created_at: datetime
    future: asyncio.Future[str]
    requester: dict[str, str]


def __getattr__(name: str) -> Any:
    if name == "THENVOI_TOOLS":
        warnings.warn(
            "THENVOI_TOOLS is deprecated, use THENVOI_ALL_TOOLS instead. "
            f"Note: this contains all {len(_THENVOI_TOOLS)} tools (chat + contacts + memory). "
            "For chat-only tools, use "
            "thenvoi.integrations.claude_sdk.tools.THENVOI_CHAT_TOOLS.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _THENVOI_TOOLS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ClaudeSDKAdapter(SimpleAdapter[ClaudeSDKSessionState]):
    """
    Claude Agent SDK adapter using SimpleAdapter pattern.

    Uses the Claude Agent SDK for LLM interactions with MCP-based tool integration.

    Note: This adapter stores tools per-room so the MCP server can access them.
    The history is converted to a text string for context injection.

    Example:
        adapter = ClaudeSDKAdapter(
            custom_section="You are a helpful assistant.",
        )
        # Or pin a model / family alias:
        # adapter = ClaudeSDKAdapter(model="opus", fallback_model="sonnet")
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.THOUGHTS}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        custom_section: str | None = None,
        max_thinking_tokens: int | None = None,
        permission_mode: PermissionMode = "acceptEdits",
        enable_execution_reporting: bool = False,
        enable_memory_tools: bool = False,
        history_converter: ClaudeSDKHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        cwd: str | None = None,
        # Chat-based approval flow (opt-in)
        approval_mode: ApprovalMode | None = None,
        approval_text_notifications: bool = True,
        approval_wait_timeout_s: float = 300.0,
        approval_timeout_decision: ApprovalDecision = "decline",
        max_pending_approvals_per_room: int = 50,
        approval_authorized_senders: set[str] | None = None,
        features: AdapterFeatures | None = None,
    ):
        """
        Initialize the Claude SDK adapter.

        Args:
            model: Claude model to use. Pass a full ID (e.g.
                ``"claude-opus-4-7-20251224"``) or a family alias
                (``"sonnet"`` / ``"opus"`` / ``"haiku"`` / ``"inherit"``).
                When ``None`` (default), the npm ``claude`` binary picks
                its own default — no ``--model`` flag is sent.
            fallback_model: Optional fallback model passed to
                ``ClaudeAgentOptions.fallback_model``. The npm ``claude``
                binary uses it when the primary model is unavailable.
                Aliases are accepted here too.
            custom_section: Custom instructions added to system prompt
            max_thinking_tokens: Max tokens for extended thinking (optional)
            permission_mode: SDK permission mode
            enable_execution_reporting: Deprecated. Use
                ``features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS})``.
                If True, emit tool_call/tool_result *and* thought events.
            enable_memory_tools: Deprecated. Use
                ``features=AdapterFeatures(capabilities={Capability.MEMORY})``.
                If True, includes memory management tools (enterprise only).
            history_converter: Optional custom history converter
            additional_tools: Optional list of custom tools as (PydanticModel, callable)
                tuples. These are converted to MCP tools internally.
            cwd: Working directory for Claude Code sessions. If set, Claude Code
                will operate in this directory (e.g., a mounted git repo).
            approval_mode: Chat-based approval mode.  ``None`` (default) disables
                chat-based approval -- the SDK's ``permission_mode`` controls
                approvals entirely.  Set to ``"manual"`` to route approval
                requests to the chat room (``/approve``, ``/decline``),
                ``"auto_accept"`` to approve everything, or ``"auto_decline"``
                to decline everything.
            approval_text_notifications: When True, send a chat message for
                auto-approve / auto-decline decisions.
            approval_wait_timeout_s: Seconds to wait for a manual approval
                before falling back to ``approval_timeout_decision``.
            approval_timeout_decision: Decision to apply when a manual approval
                times out (``"accept"`` or ``"decline"``).
            max_pending_approvals_per_room: Cap on concurrent pending approvals
                per room.  Oldest entries are evicted (declined) when full.
            approval_authorized_senders: Optional set of sender IDs allowed to
                issue ``/approve`` and ``/decline`` commands.  When ``None``
                (default), any room participant can approve.  ``/approvals``
                and ``/status`` are always available to all participants.
            features: Unified adapter feature settings. When provided alongside
                deprecated ``enable_execution_reporting`` or ``enable_memory_tools``,
                raises ``ThenvoiConfigError``.
        """
        if not _CLAUDE_SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk is required for ClaudeSDKAdapter.\n"
                "Install with: pip install thenvoi-sdk[claude_sdk]\n"
                "Or: uv add thenvoi-sdk[claude_sdk]"
            )
        # --- Shim deprecated params into features -------------------------
        _has_legacy = enable_execution_reporting or enable_memory_tools
        if _has_legacy and features is not None:
            raise ThenvoiConfigError(
                "Cannot combine 'features' with deprecated "
                "'enable_execution_reporting' / 'enable_memory_tools'. "
                "Use AdapterFeatures exclusively."
            )

        if _has_legacy:
            _emit: set[Emit] = set()
            _caps: set[Capability] = set()

            if enable_execution_reporting:
                warnings.warn(
                    "enable_execution_reporting is deprecated. "
                    "Use features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}).",
                    DeprecationWarning,
                    stacklevel=2,
                )
                # ClaudeSDK historically emitted both execution AND thought
                # events under this single flag.
                _emit.update({Emit.EXECUTION, Emit.THOUGHTS})

            if enable_memory_tools:
                warnings.warn(
                    "enable_memory_tools is deprecated. "
                    "Use features=AdapterFeatures(capabilities={Capability.MEMORY}).",
                    DeprecationWarning,
                    stacklevel=2,
                )
                _caps.add(Capability.MEMORY)

            features = AdapterFeatures(
                capabilities=frozenset(_caps),
                emit=frozenset(_emit),
            )

        super().__init__(
            history_converter=history_converter or ClaudeSDKHistoryConverter(),
            features=features,
        )

        self.model = model
        self.fallback_model = fallback_model
        self.custom_section = custom_section
        self.max_thinking_tokens = max_thinking_tokens
        self.permission_mode: ClaudeSDKAdapter.PermissionMode = permission_mode
        if cwd and not Path(cwd).is_dir():
            raise ValueError(f"cwd does not exist or is not a directory: {cwd}")
        self.cwd = cwd

        # Chat-based approval config
        self.approval_mode: ApprovalMode | None = approval_mode
        self.approval_text_notifications = approval_text_notifications
        self.approval_wait_timeout_s = approval_wait_timeout_s
        self.approval_timeout_decision: ApprovalDecision = approval_timeout_decision
        self.max_pending_approvals_per_room = max_pending_approvals_per_room
        self.approval_authorized_senders: set[str] | None = approval_authorized_senders

        # Session manager and MCP server (created after start)
        self._session_manager: ClaudeSessionManager | None = None
        self._mcp_server = None
        self._mcp_backend: ThenvoiMCPBackend | None = None

        # Per-room tools storage for MCP server access
        self._room_tools: dict[str, AgentToolsProtocol] = {}

        # Per-room session context (text history for Claude SDK)
        self._session_context: dict[str, str] = {}

        # Per-room session IDs (for SDK session resume)
        self._session_ids: dict[str, str] = {}

        # Custom tools (user-provided)
        self._custom_tools: list[CustomToolDef] = additional_tools or []

        # Approval flow state
        # {room_id: {token: _PendingApproval, ...}}
        self._pending_approvals: dict[str, dict[str, _PendingApproval]] = {}
        self._approval_seq: dict[str, int] = {}  # per-room counters
        # Last message sender per room (used for @mentions in approval notifications)
        self._room_last_sender: dict[str, dict[str, str]] = {}

    # --- Adapted from ThenvoiClaudeSDKAgent._on_started ---
    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Create MCP server and session manager after agent metadata is fetched."""
        await super().on_started(agent_name, agent_description)

        # Create MCP server with self (provides tool access via _room_tools)
        self._mcp_backend = await self._create_mcp_backend()
        self._mcp_server = self._mcp_backend.server

        # Generate system prompt with agent info
        system_prompt = generate_claude_sdk_agent_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.custom_section,
            features=self.features,
        )

        # Build SDK options. Both model and fallback_model upstream default
        # to None — passing None is equivalent to omitting them, which lets
        # the npm `claude` binary pick its own default.
        sdk_options = ClaudeAgentOptions(
            model=self.model,
            fallback_model=self.fallback_model,
            system_prompt=system_prompt,
            mcp_servers={"thenvoi": self._mcp_server},
            allowed_tools=self._mcp_backend.allowed_tools,
            permission_mode=self.permission_mode,
        )

        # Add extended thinking if configured
        if self.max_thinking_tokens:
            sdk_options.max_thinking_tokens = self.max_thinking_tokens

        # Set working directory if configured
        if self.cwd:
            sdk_options.cwd = self.cwd

        # When approval_mode is set, add a PreToolUse hook that returns
        # {"continue_": True} so the SDK delegates to can_use_tool instead
        # of auto-resolving permissions via the permission_mode.
        if self.approval_mode is not None:
            sdk_options.hooks = {
                "PreToolUse": [
                    HookMatcher(
                        hooks=[_pre_tool_use_continue_hook],
                    ),
                ],
            }

        # Create session manager (with room-specific approval callback when enabled)
        can_use_tool_factory = (
            self._make_can_use_tool if self.approval_mode is not None else None
        )
        self._session_manager = ClaudeSessionManager(
            sdk_options,
            can_use_tool_factory=can_use_tool_factory,
        )

        logger.info(
            "Claude SDK adapter started for agent: %s (model=%s, fallback_model=%s, thinking=%s, approval=%s)",
            agent_name,
            self.model or "auto",
            self.fallback_model or "none",
            self.max_thinking_tokens,
            self.approval_mode,
        )

    async def _create_mcp_backend(self) -> ThenvoiMCPBackend:
        """Create shared MCP backend that uses stored room tools."""
        include_memory = Capability.MEMORY in self.features.capabilities
        include_contacts = Capability.CONTACTS in self.features.capabilities
        tool_definitions = list(
            iter_tool_definitions(
                include_memory=include_memory,
                include_contacts=include_contacts,
            )
        )
        backend = await create_thenvoi_mcp_backend(
            kind="sdk",
            tool_definitions=tool_definitions,
            get_tools=self._room_tools.get,
            additional_tools=self._custom_tools,
        )

        logger.info(
            "Thenvoi MCP SDK server created with %s tools (%s custom)",
            len(backend.allowed_tools),
            len(self._custom_tools),
        )

        return backend

    # --- Adapted from ThenvoiClaudeSDKAgent._handle_message ---
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: ClaudeSDKSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """
        Handle incoming message.

        - Store tools for MCP server access
        - Get or create ClaudeSDKClient for this room
        - Include room_id in the message so Claude can pass it to tools
        - Stream response and log events (tools execute via MCP)
        """
        logger.debug("Handling message %s in room %s", msg.id, room_id)

        if not self._session_manager:
            raise RuntimeError(
                "ClaudeSDKAdapter session manager not initialized — was on_started() called?"
            )

        # Store tools for MCP server access
        self._room_tools[room_id] = tools

        # Approval flow: track notify target and intercept local commands
        if self.approval_mode is not None:
            self._room_last_sender[room_id] = {
                "id": msg.sender_id,
                "name": msg.sender_name or msg.sender_type,
            }
            command = self._extract_command(msg.content)
            if command is not None:
                cmd, args = command
                sender = self._room_last_sender[room_id]
                if cmd in _APPROVAL_CMDS:
                    await self._handle_approval_command(
                        tools=tools,
                        room_id=room_id,
                        command=cmd,
                        args=args,
                        sender=sender,
                    )
                    return
                elif cmd == "status":
                    await self._handle_status_command(
                        tools=tools,
                        room_id=room_id,
                        sender=sender,
                    )
                    return

        # Determine session_id for resume: prefer history (persisted) then
        # in-memory cache.  Only used on bootstrap/reconnect.
        stored_session_id: str | None = None
        if is_session_bootstrap:
            stored_session_id = history.session_id or self._session_ids.get(room_id)

        # Get or create Claude SDK client for this room (optionally resuming)
        try:
            client = await self._session_manager.get_or_create_session(
                room_id, resume_session_id=stored_session_id
            )
        except Exception as resume_exc:
            if stored_session_id:
                logger.warning(
                    "Room %s: Session resume failed (session_id=%s): %s. "
                    "Creating new session",
                    room_id,
                    stored_session_id,
                    resume_exc,
                )
                client = await self._session_manager.get_or_create_session(
                    room_id, resume_session_id=None
                )
            else:
                raise

        # Add room_id context (Claude needs this for tool calls)
        room_context = f"[room_id: {room_id}]"

        # Initialize history for this room on first message
        if is_session_bootstrap:
            if history.text:  # Already converted to text by SimpleAdapter
                self._session_context[room_id] = history.text
                logger.info(
                    "Room %s: Loaded historical context (%s chars)",
                    room_id,
                    len(history.text),
                )
            else:
                self._session_context[room_id] = ""
        elif room_id not in self._session_context:
            # Safety: ensure context exists even if not first message
            self._session_context[room_id] = ""

        # Build message with context
        messages_to_send = []

        # Include historical context on first message
        if is_session_bootstrap and self._session_context.get(room_id):
            messages_to_send.append(
                f"[Previous conversation context:]\n{self._session_context[room_id]}"
            )

        # Inject participants message if changed
        if participants_msg:
            messages_to_send.append(f"{room_context}[System]: {participants_msg}")
            logger.info("Room %s: Participants updated", room_id)

        # Inject contacts message if present
        if contacts_msg:
            messages_to_send.append(f"{room_context}[System]: {contacts_msg}")
            logger.info("Room %s: Contacts broadcast received", room_id)

        # Add current message with room_id context
        user_message = f"{room_context}{msg.format_for_llm()}"
        messages_to_send.append(user_message)

        # Send combined message to Claude
        full_message = "\n\n".join(messages_to_send)

        logger.info(
            "Room %s: Sending query to Claude SDK (first_msg=%s, parts=%s)",
            room_id,
            is_session_bootstrap,
            len(messages_to_send),
        )

        try:
            # Send query to Claude
            await client.query(full_message)

            # Process streaming response (MCP tools handle execution)
            await self._process_response(client, room_id, tools)

        except CLIConnectionError as e:
            # CLI process is dead — evict the cached session so the next
            # message creates a fresh one instead of reusing the corpse.
            logger.error(
                "Room %s: CLI process terminated: %s — invalidating session",
                room_id,
                e,
            )
            await self._session_manager.invalidate_session(room_id)
            self._session_ids.pop(room_id, None)

            await self._report_error(tools, str(e))
            raise

        except Exception as e:
            logger.exception("Error processing message: %s", e)
            await self._report_error(tools, str(e))
            raise

        logger.debug("Message %s processed successfully", msg.id)

    # --- Copied from ThenvoiClaudeSDKAgent._process_response ---
    async def _process_response(
        self, client: ClaudeSDKClient, room_id: str, tools: AgentToolsProtocol
    ) -> None:
        """
        Process streaming response from Claude SDK.

        MCP tools handle actual execution - we log and optionally report events here.
        """
        async for sdk_message in client.receive_response():
            if isinstance(sdk_message, AssistantMessage):
                for block in sdk_message.content:
                    if isinstance(block, TextBlock):
                        if block.text:
                            logger.debug(
                                "Room %s: Text: %s...", room_id, block.text[:100]
                            )

                    elif isinstance(block, ThinkingBlock):
                        if block.thinking:
                            logger.debug(
                                "Room %s: Thinking: %s...",
                                room_id,
                                block.thinking[:100],
                            )
                            # Report thinking as event if enabled
                            if Emit.THOUGHTS in self.features.emit:
                                try:
                                    await tools.send_event(
                                        content=block.thinking,
                                        message_type="thought",
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "Failed to send thinking event: %s", e
                                    )

                    elif isinstance(block, ToolUseBlock):
                        logger.info(
                            "Room %s: Tool call: %s with %s...",
                            room_id,
                            block.name,
                            str(block.input)[:100],
                        )
                        if Emit.EXECUTION in self.features.emit:
                            try:
                                await tools.send_event(
                                    content=json.dumps(
                                        {
                                            "name": block.name,
                                            "args": block.input,
                                            "tool_call_id": block.id,
                                        }
                                    ),
                                    message_type="tool_call",
                                )
                            except Exception as e:
                                logger.warning("Failed to send tool_call event: %s", e)

                    elif isinstance(block, ToolResultBlock):
                        logger.debug(
                            "Room %s: Tool result: %s... error=%s",
                            room_id,
                            block.tool_use_id[:20],
                            block.is_error,
                        )
                        if Emit.EXECUTION in self.features.emit:
                            try:
                                await tools.send_event(
                                    content=json.dumps(
                                        {
                                            "output": block.content,
                                            "tool_call_id": block.tool_use_id,
                                        }
                                    ),
                                    message_type="tool_result",
                                )
                            except Exception as e:
                                logger.warning(
                                    "Failed to send tool_result event: %s", e
                                )

            elif isinstance(sdk_message, ResultMessage):
                logger.info(
                    "Room %s: Complete - %sms, $%.4f",
                    room_id,
                    sdk_message.duration_ms,
                    sdk_message.total_cost_usd or 0,
                )
                # Capture session_id for potential resume
                if sdk_message.session_id:
                    prev_session_id = self._session_ids.get(room_id)
                    self._session_ids[room_id] = sdk_message.session_id
                    logger.debug(
                        "Room %s: Captured session_id %s",
                        room_id,
                        sdk_message.session_id,
                    )
                    # Persist session_id as task event (best-effort, only on change)
                    if sdk_message.session_id != prev_session_id:
                        try:
                            await tools.send_event(
                                content="Claude SDK session",
                                message_type="task",
                                metadata={
                                    "claude_sdk_session_id": sdk_message.session_id,
                                },
                            )
                        except Exception as e:
                            logger.warning(
                                "Room %s: Failed to persist session_id: %s",
                                room_id,
                                e,
                            )
                break

    # --- Copied from ThenvoiClaudeSDKAgent._cleanup_session ---
    async def on_cleanup(self, room_id: str) -> None:
        """Clean up Claude SDK session and stored tools when agent leaves a room."""
        self._clear_pending_approvals_for_room(room_id)
        if self._session_manager:
            await self._session_manager.cleanup_session(room_id)
        self._room_tools.pop(room_id, None)
        self._session_context.pop(room_id, None)
        self._session_ids.pop(room_id, None)
        self._room_last_sender.pop(room_id, None)
        logger.debug("Room %s: Cleaned up Claude SDK session", room_id)

    # --- Copied from BaseFrameworkAgent._report_error ---
    async def _report_error(self, tools: AgentToolsProtocol, error: str) -> None:
        """Send error event (best effort)."""
        try:
            await tools.send_event(content=f"Error: {error}", message_type="error")
        except Exception:
            logger.debug("Failed to send error event", exc_info=True)

    async def cleanup_all(self) -> None:
        """Cleanup all sessions (call on stop)."""
        # Decline all pending approvals across rooms
        for room_id in list(self._pending_approvals):
            self._clear_pending_approvals_for_room(room_id)
        if self._session_manager:
            await self._session_manager.stop()
        if self._mcp_backend:
            await self._mcp_backend.stop()
            self._mcp_backend = None
            self._mcp_server = None
        self._room_tools.clear()
        self._session_context.clear()
        self._session_ids.clear()
        self._room_last_sender.clear()

    # ------------------------------------------------------------------
    # Chat-based approval flow
    # ------------------------------------------------------------------

    def _make_can_use_tool(self, room_id: str) -> CanUseTool:
        """Return a room-bound ``can_use_tool`` callback for the Claude SDK."""

        async def _can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            summary = self._approval_summary(tool_name, tool_input)
            # Capture the sender now so it doesn't get overwritten by
            # messages arriving while we wait for a decision.
            requester = self._room_last_sender.get(room_id)
            logger.debug(
                "can_use_tool: %s in room %s (mode=%s)",
                tool_name,
                room_id,
                self.approval_mode,
            )

            # --- auto modes ---------------------------------------------------
            if self.approval_mode == "auto_accept":
                if self.approval_text_notifications:
                    await self._notify_auto_decision(
                        room_id, summary, "accept", requester=requester
                    )
                return PermissionResultAllow()

            if self.approval_mode == "auto_decline":
                if self.approval_text_notifications:
                    await self._notify_auto_decision(
                        room_id, summary, "decline", requester=requester
                    )
                return PermissionResultDeny(
                    message=f"Tool use declined by policy: {summary}"
                )

            # --- manual mode ---------------------------------------------------
            return await self._resolve_manual_approval(
                room_id, tool_name, tool_input, summary, requester=requester
            )

        return _can_use_tool

    async def _notify_auto_decision(
        self,
        room_id: str,
        summary: str,
        decision: str,
        *,
        requester: dict[str, str] | None = None,
    ) -> None:
        """Best-effort chat notification for auto-approve / auto-decline."""
        tools = self._room_tools.get(room_id)
        if not tools:
            return
        mention = [requester["id"]] if requester else None
        try:
            await tools.send_message(
                f"Approval requested ({summary}). Policy decision: **{decision}**.",
                mentions=mention,
            )
        except Exception as e:
            logger.warning("Failed to send approval policy notification: %s", e)

    async def _resolve_manual_approval(
        self,
        room_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        summary: str,
        *,
        requester: dict[str, str] | None = None,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Block until a user approves / declines via ``/approve`` or ``/decline``."""
        loop = asyncio.get_running_loop()
        token = self._next_approval_token(room_id)

        pending = _PendingApproval(
            tool_name=tool_name,
            tool_input=tool_input,
            summary=summary,
            created_at=datetime.now(timezone.utc),
            future=loop.create_future(),
            requester=requester or {"id": "", "name": ""},
        )

        # Store pending approval (evict oldest if capacity exceeded)
        room_pending = self._pending_approvals.setdefault(room_id, {})
        if len(room_pending) >= self.max_pending_approvals_per_room:
            oldest_token = min(room_pending, key=lambda t: room_pending[t].created_at)
            oldest = room_pending.pop(oldest_token)
            if not oldest.future.done():
                oldest.future.set_result("decline")
            logger.warning(
                "Room %s: Evicted oldest pending approval %s (capacity %s)",
                room_id,
                oldest_token,
                self.max_pending_approvals_per_room,
            )
        room_pending[token] = pending

        # Notify user — if we can't deliver the prompt, decline immediately
        # so the caller isn't left waiting for a timeout nobody will see.
        tools = self._room_tools.get(room_id)
        mention = [requester["id"]] if requester else None
        if tools:
            try:
                await tools.send_message(
                    f"Approval requested ({summary}). Token: `{token}`.\n"
                    f"Reply `/approve {token}` or `/decline {token}`.\n"
                    "Use `/approvals` to list pending approvals.",
                    mentions=mention,
                )
            except Exception:
                logger.warning(
                    "Room %s: Failed to send approval notification — declining", room_id
                )
                self._clear_pending_approval(room_id, token)
                return PermissionResultDeny(
                    message="Could not deliver approval prompt, tool use declined"
                )

        # Wait for decision or timeout
        try:
            decision_raw = await asyncio.wait_for(
                pending.future,
                timeout=self.approval_wait_timeout_s,
            )
            if decision_raw == "accept":
                return PermissionResultAllow()
            return PermissionResultDeny(message="User declined tool use")

        except asyncio.TimeoutError:
            decision: ApprovalDecision = self.approval_timeout_decision
            if tools:
                try:
                    await tools.send_message(
                        f"Approval `{token}` timed out. Decision: **{decision}**.",
                        mentions=mention,
                    )
                except Exception:
                    logger.debug(
                        "Room %s: Failed to send timeout notification", room_id
                    )

            if decision == "accept":
                return PermissionResultAllow()
            return PermissionResultDeny(message="Approval timed out, tool use declined")

        finally:
            self._clear_pending_approval(room_id, token)

    # ------------------------------------------------------------------
    # Command extraction & handling
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_command(content: str) -> tuple[str, str] | None:
        """Check if *content* starts with a ``/command``.

        Only the first token is considered to avoid false positives from
        natural language like ``"don't /decline it"``.
        Returns ``(command, rest)`` or ``None``.
        """
        stripped = content.lstrip()
        if not stripped.startswith("/"):
            return None
        token, _, rest = stripped.partition(" ")
        clean = token[1:]
        if clean.lower() in _LOCAL_CMDS:
            return (clean.lower(), rest.strip())
        return None

    async def _handle_approval_command(
        self,
        tools: AgentToolsProtocol,
        room_id: str,
        command: str,
        args: str,
        sender: dict[str, str],
    ) -> None:
        """Handle ``/approve``, ``/decline``, or ``/approvals``."""
        # This is a reference to the live mutable dict for the room (or an
        # empty dict if none exists).  Safe because the event loop is
        # single-threaded, so no concurrent mutation can occur mid-handler.
        pending = self._pending_approvals.get(room_id, {})
        mention: list[str] = [sender["id"]]

        # Authorization: /approve and /decline require sender to be authorized
        if command in ("approve", "decline") and self.approval_authorized_senders:
            if sender["id"] not in self.approval_authorized_senders:
                await tools.send_message(
                    "You are not authorized to approve or decline tool use.",
                    mentions=mention,
                )
                return

        # --- /approvals: list pending ---
        if command == "approvals":
            if not pending:
                await tools.send_message("No pending approvals.", mentions=mention)
                return
            lines = ["Pending approvals:"]
            now = datetime.now(timezone.utc)
            for token, item in list(pending.items()):
                age_s = int((now - item.created_at).total_seconds())
                lines.append(f"- `{token}`: {item.summary} ({age_s}s ago)")
            await tools.send_message("\n".join(lines), mentions=mention)
            return

        # --- /approve [token] | /decline [token] ---
        token = args.strip() if args else ""
        selected: _PendingApproval | None = None

        if token:
            selected = pending.get(token)
            if not selected:
                available = ", ".join(f"`{t}`" for t in pending) if pending else "none"
                await tools.send_message(
                    f"Unknown approval token `{token}`. Available: {available}.",
                    mentions=mention,
                )
                return
        elif len(pending) == 1:
            token, selected = next(iter(pending.items()))
        elif len(pending) == 0:
            await tools.send_message("No pending approvals.", mentions=mention)
            return
        else:
            tokens_list = ", ".join(f"`{t}`" for t in pending)
            await tools.send_message(
                f"Multiple pending approvals — please specify a token: {tokens_list}",
                mentions=mention,
            )
            return

        decision: ApprovalDecision = "accept" if command == "approve" else "decline"
        if not selected.future.done():
            selected.future.set_result(decision)
        await tools.send_message(
            f"Approval `{token}` resolved as **{decision}**.",
            mentions=mention,
        )

    async def _handle_status_command(
        self,
        tools: AgentToolsProtocol,
        room_id: str,
        sender: dict[str, str],
    ) -> None:
        """Handle the ``/status`` command."""
        session_count = (
            self._session_manager.get_session_count() if self._session_manager else 0
        )
        session_id = self._session_ids.get(room_id, "—")
        pending_count = len(self._pending_approvals.get(room_id, {}))

        lines = [
            "**Claude SDK Status**",
            f"- model: `{self.model or 'auto'}`",
            f"- fallback_model: `{self.fallback_model or 'none'}`",
            f"- permission_mode: `{self.permission_mode}`",
            f"- approval_mode: `{self.approval_mode or 'disabled'}`",
            f"- pending_approvals: {pending_count}",
            f"- active_sessions: {session_count}",
            f"- session_id: `{session_id}`",
        ]
        await tools.send_message("\n".join(lines), mentions=[sender["id"]])

    # ------------------------------------------------------------------
    # Approval helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _redact_command(command: str) -> str:
        """Redact values that look like secrets from a shell command."""
        return _REDACT_RE.sub(
            lambda m: (
                m.group(0).split("=")[0] + "=***"
                if "=" in m.group(0)
                else m.group(0).split(":")[0] + ":***"
            ),
            command,
        )

    @staticmethod
    def _approval_summary(tool_name: str, tool_input: dict[str, Any]) -> str:
        """Build a human-readable one-line summary for an approval request.

        Sensitive-looking values (tokens, passwords, keys) are redacted to
        avoid leaking secrets into the chat room.
        """
        # For shell commands, show the command (redacted)
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            safe = ClaudeSDKAdapter._redact_command(command)[:120]
            return f"{tool_name}: `{safe}`"
        # For file edits, show the path
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(file_path, str) and file_path:
            return f"{tool_name}: {file_path}"
        return tool_name

    def _next_approval_token(self, room_id: str) -> str:
        """Generate a short, per-room incrementing approval token."""
        seq = self._approval_seq.get(room_id, 0) + 1
        self._approval_seq[room_id] = seq
        return f"a-{seq}"

    def _clear_pending_approval(self, room_id: str, token: str) -> None:
        """Remove a single pending approval from a room."""
        room_pending = self._pending_approvals.get(room_id)
        if not room_pending:
            return
        room_pending.pop(token, None)
        if not room_pending:
            self._pending_approvals.pop(room_id, None)

    def _clear_pending_approvals_for_room(self, room_id: str) -> None:
        """Decline and remove all pending approvals for a room."""
        room_pending = self._pending_approvals.pop(room_id, {})
        for item in room_pending.values():
            if not item.future.done():
                item.future.set_result("decline")
        # Keep the seq counter to avoid token collisions with suspended coroutines
