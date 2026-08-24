"""
Claude SDK adapter using SimpleAdapter pattern.

Extracted from band.integrations.claude_sdk.agent.BandClaudeSDKAgent.

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
from collections.abc import Callable
from typing import Any, ClassVar, Literal, cast

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
        UserMessage,
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

from typing_extensions import Unpack

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    ToolEventKey,
    TurnUsage,
)
from band.converters.claude_sdk import (
    SESSION_ID_METADATA_KEY,
    ClaudeSDKHistoryConverter,
    ClaudeSDKSessionState,
)
from band.integrations.mcp.backends import (
    BandMCPBackend,
    create_band_mcp_backend,
)
from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from band.integrations.claude_sdk.prompts import generate_claude_sdk_agent_prompt
from band.integrations.claude_sdk.dedup_tools import (
    DEFAULT_DEDUP_TTL_SECONDS,
    DedupingAgentTools,
)
from band.runtime.custom_tools import (
    CustomToolDef,
    get_custom_tool_name,
    is_marked_terminal,
)
from band.runtime.formatters import strip_leading_mentions
from band.runtime.tools import (
    ALL_TOOL_NAMES,
    BASE_TOOL_NAMES,
    CHAT_ID_FIELD_NAME,
    MAX_INLINE_IMAGE_BYTES,
    MCP_TOOL_PREFIX,
    MEMORY_TOOL_NAMES,
    band_tool_errored,
    is_terminal_success,
    iter_tool_definitions,
    mcp_tool_names,
    missing_reply_error,
)

logger = logging.getLogger(__name__)


# Tool names as constants (MCP naming convention: mcp__{server}__{tool})
# Derived from TOOL_MODELS — single source of truth
BAND_BASE_TOOLS: list[str] = mcp_tool_names(BASE_TOOL_NAMES)
BAND_MEMORY_TOOLS: list[str] = mcp_tool_names(MEMORY_TOOL_NAMES)
# All tools: chat + contacts + memory + files (20 total). For chat-only
# tools (7), see band.integrations.claude_sdk.tools.BAND_CHAT_TOOLS.
BAND_ALL_TOOLS: list[str] = mcp_tool_names(ALL_TOOL_NAMES)

_BAND_TOOLS: list[str] = BAND_ALL_TOOLS

# Default model used when the caller does not specify one. Letting the npm
# `claude` CLI auto-select its default fails under API-key auth: the CLI sends
# the legacy `thinking.type.enabled` request shape, which current models reject
# ("thinking.type.enabled is not supported for this model. Use
# thinking.type.adaptive"), so the run returns an error result with no output.
# Pinning a known-good model avoids that path; callers can override via `model=`.
_DEFAULT_MODEL = "claude-sonnet-4-6"

# claude_agent_sdk's stdio transport defaults max_buffer_size to 1 MiB and
# fatally drops the whole CLI connection (not just the one tool call) if a
# single JSON-per-line message from the CLI exceeds it. band_read_room_file
# inlines images up to MAX_INLINE_IMAGE_BYTES as base64 (~4/3 size increase)
# inside that message, so an image well under our own advertised cap can
# already exceed the library's unrelated default. Size the buffer off the
# same constant instead of a second, driftable number.
_CLAUDE_SDK_MAX_BUFFER_BYTES = MAX_INLINE_IMAGE_BYTES * 2

# Approval flow types (mirrors Codex adapter patterns)
ApprovalMode = Literal["auto_accept", "auto_decline", "manual"]
ApprovalDecision = Literal["accept", "decline"]

# Commands recognised as local (not forwarded to Claude)
_APPROVAL_CMDS = frozenset({"approve", "decline", "approvals"})
_LOCAL_CMDS = _APPROVAL_CMDS | frozenset({"status"})

# A pending approval's future, force-resolved by eviction or room teardown
# rather than a genuine /decline reply — distinct from the "decline" string
# _handle_approval_command sets, since only that path posts a room-visible
# notice for the specific call it declines (see _record_notified_decline).
_FORCED_DECLINE = "forced_decline"

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


def _redact_image_data(content: str | list[dict[str, Any]] | None) -> Any:
    """Replace an image content block's base64 payload before narration.

    ``band_read_room_file``'s image branch hands the model a real MCP image
    block (``{"type": "image", "data": <base64>, ...}``) round-tripped back
    unchanged in ``ToolResultBlock.content`` -- exactly what the vision fix
    intends. But this narration event is a room-visible log, not the model
    input: dumping the raw base64 into it would bloat the room's stored
    history with megabytes of text nobody reads. Only the size survives.
    """
    if not isinstance(content, list):
        return content
    return [
        {**block, "data": f"<{len(block['data'])} base64 chars omitted>"}
        if isinstance(block, dict) and isinstance(block.get("data"), str)
        else block
        for block in content
    ]


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
class PendingApproval:
    """A tool-use approval request waiting for a chat-room decision."""

    tool_name: str
    tool_input: dict[str, Any]
    summary: str
    created_at: datetime
    future: asyncio.Future[str]
    requester: dict[str, str]


def __getattr__(name: str) -> Any:
    if name == "BAND_TOOLS":
        warnings.warn(
            "BAND_TOOLS is deprecated, use BAND_ALL_TOOLS instead. "
            f"Note: this contains all {len(_BAND_TOOLS)} tools (chat + contacts + memory + files). "
            "For chat-only tools, use "
            "band.integrations.claude_sdk.tools.BAND_CHAT_TOOLS.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _BAND_TOOLS
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
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.FILES}
    )

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        custom_section: str | None = None,
        max_thinking_tokens: int | None = None,
        permission_mode: PermissionMode = "acceptEdits",
        history_converter: ClaudeSDKHistoryConverter | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        cwd: str | None = None,
        setting_sources: list[str] | None = None,
        # Chat-based approval flow (opt-in)
        approval_mode: ApprovalMode | None = None,
        approval_text_notifications: bool = True,
        approval_wait_timeout_s: float = 300.0,
        approval_timeout_decision: ApprovalDecision = "decline",
        max_pending_approvals_per_room: int = 50,
        approval_authorized_senders: set[str] | None = None,
        send_message_dedup_ttl_seconds: float = DEFAULT_DEDUP_TTL_SECONDS,
        **features: Unpack[FeatureKwargs],
    ):
        """
        Initialize the Claude SDK adapter.

        Args:
            model: Claude model to use. Pass a full ID (e.g.
                ``"claude-opus-4-7-20251224"``) or a family alias
                (``"sonnet"`` / ``"opus"`` / ``"haiku"`` / ``"inherit"``).
                When ``None`` (default), the adapter pins ``_DEFAULT_MODEL``
                rather than letting the npm ``claude`` binary auto-select,
                which fails under API-key auth (legacy thinking request shape).
            fallback_model: Optional fallback model passed to
                ``ClaudeAgentOptions.fallback_model``. The npm ``claude``
                binary uses it when the primary model is unavailable.
                Aliases are accepted here too.
            custom_section: Custom instructions added to system prompt
            max_thinking_tokens: Max tokens for extended thinking (optional)
            permission_mode: SDK permission mode
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
            send_message_dedup_ttl_seconds: Window (seconds) inside which two
                ``band_send_message`` MCP tool calls with identical
                ``(content, mentions)`` are collapsed into one platform POST.
                Mitigates duplicate-message events caused by Claude CLI / MCP
                transport retries when the event loop is stalled.
                Defaults to 30 s; set to ``0`` to disable dedup entirely.
        """
        if not _CLAUDE_SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk is required for ClaudeSDKAdapter.\n"
                "Install with: pip install band-sdk[claude_sdk]\n"
                "Or: uv add band-sdk[claude_sdk]"
            )
        super().__init__(
            history_converter=history_converter or ClaudeSDKHistoryConverter(),
            **features,
        )

        self.model = model
        self.fallback_model = fallback_model
        self.custom_section = custom_section
        self.max_thinking_tokens = max_thinking_tokens
        self.permission_mode: ClaudeSDKAdapter.PermissionMode = permission_mode
        if cwd and not Path(cwd).is_dir():
            raise ValueError(f"cwd does not exist or is not a directory: {cwd}")
        self.cwd = cwd
        # Which host settings the CLI loads (skills/subagents/settings from
        # ~/.claude and ./.claude). Default isolates the bridged agent so its
        # capabilities are defined here, not by whatever config sits on the host
        # (the source of Windows-vs-Linux tool drift). Pass e.g. ["user", "project"]
        # to opt back into host config.
        self.setting_sources: list[str] = (
            list(setting_sources) if setting_sources is not None else []
        )

        # Chat-based approval config
        self.approval_mode: ApprovalMode | None = approval_mode
        self.approval_text_notifications = approval_text_notifications
        self.approval_wait_timeout_s = approval_wait_timeout_s
        self.approval_timeout_decision: ApprovalDecision = approval_timeout_decision
        self.max_pending_approvals_per_room = max_pending_approvals_per_room
        self.approval_authorized_senders: set[str] | None = approval_authorized_senders

        # send_message dedup window.  0 disables the wrapper.
        if send_message_dedup_ttl_seconds < 0:
            raise ValueError("send_message_dedup_ttl_seconds must be >= 0")
        self.send_message_dedup_ttl_seconds = send_message_dedup_ttl_seconds

        # Session manager and MCP server (created after start)
        self._session_manager: ClaudeSessionManager | None = None
        self._mcp_server = None
        self._mcp_backend: BandMCPBackend | None = None

        # Per-room tools storage for MCP server access
        self._room_tools: dict[str, AgentToolsProtocol] = {}

        # Per-room session context (text history for Claude SDK)
        self._session_context: dict[str, str] = {}

        # Per-room session IDs (for SDK session resume)
        self._session_ids: dict[str, str] = {}

        # Custom tools (user-provided)
        self._custom_tools: list[CustomToolDef] = additional_tools or []
        # Custom tools that opt in as terminal actions (band_terminal=True on the
        # handler). Only these let a turn with no Band terminal tool call still
        # count as answered — see is_terminal_success. Keyed by the name the
        # tool is actually registered/called under (get_custom_tool_name), not
        # the handler's Python __name__ — _build_custom_sdk_tool derives the
        # MCP tool name from the input model, so the two can differ.
        self._custom_terminal_names: frozenset[str] = frozenset(
            get_custom_tool_name(input_model)
            for input_model, handler in self._custom_tools
            if is_marked_terminal(handler)
        )

        # Approval flow state
        # {room_id: {token: PendingApproval, ...}}
        self._pending_approvals: dict[str, dict[str, PendingApproval]] = {}
        self._approval_seq: dict[str, int] = {}  # per-room counters
        # Last message sender per room (used for @mentions in approval notifications)
        self._room_last_sender: dict[str, dict[str, str]] = {}
        # tool_use_ids (per room) whose decline was actually posted as a
        # room-visible notice this turn (see _record_notified_decline). Popped
        # unconditionally once per turn in _on_turn_complete — see
        # _declined_the_reply for what this is cross-referenced against and
        # why tool_use_id uniqueness needs no turn-scoping here.
        self._notified_declines: dict[str, set[str]] = {}
        # Bare tool name per pending call, keyed by room then tool_use_id
        # (ToolResultBlock only carries the id). Room-scoped rather than
        # turn-local: a resumed session can replay a result whose tool_use
        # streamed in an earlier, truncated turn, and that result must still
        # resolve to its name to count as terminal work. Entries are popped
        # as results arrive and the room's map is dropped in on_cleanup.
        self._pending_tool_names: dict[str, dict[str, str]] = {}

    # --- Adapted from BandClaudeSDKAgent._on_started ---
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

        # Build SDK options. When the caller doesn't pin a model, default to a
        # known-good one rather than the npm `claude` binary's auto-selection,
        # which fails under API-key auth (see _DEFAULT_MODEL). fallback_model
        # stays None unless explicitly set.
        resolved_model = self.model or _DEFAULT_MODEL
        sdk_options = ClaudeAgentOptions(
            model=resolved_model,
            fallback_model=self.fallback_model,
            system_prompt=system_prompt,
            mcp_servers={"band": self._mcp_server},
            allowed_tools=self._mcp_backend.allowed_tools,
            permission_mode=self.permission_mode,
            max_buffer_size=_CLAUDE_SDK_MAX_BUFFER_BYTES,
            # Isolate the bridged agent from ambient Claude Code config (default []).
            # Left at the SDK default, setting_sources loads the host's user + project
            # settings (~/.claude and ./.claude): filesystem skills and subagents then
            # surface as `Skill`/`Agent` tools, and the enlarged toolset trips
            # ToolSearch, which withholds tool definitions (including our `mcp__band__*`
            # tools) behind a search step — so on a CI runner with a global Claude Code
            # install the model wandered through ToolSearch/Bash/Agent/Skill instead of
            # calling the Band tool. Loading no host config keeps the tool set lean, so
            # ToolSearch does not engage and the Band tools stay directly in context.
            # Configurable via the constructor for callers who want their host config.
            # cast: the public param is list[str]; the SDK types it as a list of the
            # "user"/"project"/"local" literals. The CLI validates the values.
            setting_sources=cast("Any", self.setting_sources),
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
            resolved_model,
            self.fallback_model or "none",
            self.max_thinking_tokens,
            self.approval_mode,
        )

    async def _create_mcp_backend(self) -> BandMCPBackend:
        """Create shared MCP backend that uses stored room tools."""
        tool_definitions = list(
            iter_tool_definitions(capabilities=self.features.capabilities)
        )
        backend = await create_band_mcp_backend(
            kind="sdk",
            tool_definitions=tool_definitions,
            get_tools=self._room_tools.get,
            additional_tools=self._custom_tools,
        )

        logger.info(
            "Band MCP SDK server created with %s tools (%s custom)",
            len(backend.allowed_tools),
            len(self._custom_tools),
        )

        return backend

    # --- Adapted from BandClaudeSDKAgent._handle_message ---
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
        - Include chat_id in the message so Claude can pass it to tools
        - Stream response and log events (tools execute via MCP)
        """
        logger.debug("Handling message %s in room %s", msg.id, room_id)

        if not self._session_manager:
            raise RuntimeError(
                "ClaudeSDKAdapter session manager not initialized — was on_started() called?"
            )

        # Store tools for MCP server access.  Wrap with the send_message
        # dedup shim so MCP-driven retries (event-loop saturation
        # under Claude CLI load causes the same band_send_message tool
        # call to fire more than once for a single LLM-intended send) do
        # not turn into duplicate chat messages.  Bypass the wrapper when
        # the operator has explicitly opted out via ttl=0.
        #
        # The wrapper MUST persist for the room so a lingering MCP retry can
        # still see the cache through self._room_tools.get. MCP tool calls
        # only resolve by room id, not by the original inbound message id, so
        # the cache is intentionally keyed by the outgoing payload within the
        # per-room wrapper. Swap the inner reference instead of rebuilding the
        # wrapper.
        #
        # DedupingAgentTools is structurally a superset of AgentToolsProtocol
        # (the dedup shim only intercepts send_message and __getattr__-forwards
        # everything else), but pyrefly cannot reason about __getattr__ for
        # protocol conformance, so we cast through Any.
        if self.send_message_dedup_ttl_seconds > 0:
            existing = self._room_tools.get(room_id)
            if isinstance(existing, DedupingAgentTools):
                if existing._inner is not tools:
                    await existing.update_inner(tools)
                tools = cast(AgentToolsProtocol, existing)
            else:
                wrapper = DedupingAgentTools(
                    tools,
                    ttl_seconds=self.send_message_dedup_ttl_seconds,
                    label=room_id,
                )
                tools = cast(AgentToolsProtocol, wrapper)
                self._room_tools[room_id] = tools
        else:
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

        # Add chat_id context (Claude needs this for tool calls) -- the label
        # must read "chat_id" (the model-facing name everywhere else), not
        # the Python-side room_id it's built from.
        room_context = f"[{CHAT_ID_FIELD_NAME}: {room_id}]"

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

        # Include historical context on first message. Frame it as authoritative
        # memory, not a passive quote: under the claude_code preset the model treats a
        # "previous context" aside weakly, so a fact another participant stated (or that
        # you stated) while you were offline gets missed on recall. Tell it plainly this
        # is its own memory of the room and to answer from it.
        if is_session_bootstrap and self._session_context.get(room_id):
            messages_to_send.append(
                "Your memory of this room so far — real earlier messages from you and "
                "from other participants and agents, including ones sent while you were "
                "offline. Treat these as facts you know and answer questions about the "
                f"conversation directly from them:\n"
                f"{self._session_context[room_id]}"
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
            await self._invalidate_session(room_id)

            await self._report_error(tools, str(e))
            raise

        except Exception as e:
            logger.exception("Error processing message: %s", e)
            await self._report_error(tools, str(e))
            raise

        logger.debug("Message %s processed successfully", msg.id)

    async def _invalidate_session(self, room_id: str) -> None:
        """Evict the cached session and client so the next message for this
        room creates a fresh one instead of reusing a corpse."""
        if self._session_manager:
            await self._session_manager.invalidate_session(room_id)
        self._session_ids.pop(room_id, None)

    async def _process_response(
        self, client: ClaudeSDKClient, room_id: str, tools: AgentToolsProtocol
    ) -> None:
        """Dispatch the turn's streamed messages until its terminal ResultMessage.

        MCP tools handle actual execution - we log and optionally report events here.
        """
        # The room's pending-call map (see __init__) — persists across turns
        # so a result replayed by a resumed session still resolves its name.
        pending_tool_names = self._pending_tool_names.setdefault(room_id, {})
        replied_this_turn = False
        async for sdk_message in client.receive_response():
            match sdk_message:
                case AssistantMessage():
                    replied_this_turn |= await self._on_assistant_message(
                        sdk_message, pending_tool_names, room_id, tools
                    )
                case UserMessage():
                    replied_this_turn |= await self._on_user_message(
                        sdk_message, pending_tool_names, room_id, tools
                    )
                case ResultMessage():
                    await self._on_turn_complete(
                        sdk_message,
                        room_id,
                        tools,
                        replied_this_turn=replied_this_turn,
                    )
                    return
        # ``receive_response`` is documented to terminate only after its
        # ResultMessage. Reaching EOF first means the CLI transport died.
        if replied_this_turn:
            # The reply already reached the room — failing the turn would
            # make the runtime redeliver the message and answer the user
            # twice. Drop the dead session and treat the turn as done.
            logger.warning(
                "Room %s: CLI stream ended without a result after the "
                "reply was delivered — invalidating session",
                room_id,
            )
            await self._invalidate_session(room_id)
            return
        # Nothing was delivered: use the normal dead-client path so the
        # runtime marks this turn failed and the cached client is not reused.
        raise CLIConnectionError(self._stream_ended_without_result_error())

    async def _on_assistant_message(
        self,
        message: AssistantMessage,
        pending_tool_names: dict[str, str],
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> bool:
        """Narrate one assistant message's blocks.

        Returns True when a carried tool result was terminal work — results
        normally arrive in user envelopes (see _on_user_message);
        assistant-carried ones are accepted defensively.
        """
        replied_this_turn = False
        for block in message.content:
            match block:
                case TextBlock() if block.text:
                    logger.debug("Room %s: Text: %s...", room_id, block.text[:100])
                case ThinkingBlock() if block.thinking:
                    await self._narrate_thinking(block, room_id, tools)
                case _:
                    replied_this_turn |= await self._dispatch_tool_block(
                        block, pending_tool_names, room_id, tools
                    )
        return replied_this_turn

    async def _on_user_message(
        self,
        message: UserMessage,
        pending_tool_names: dict[str, str],
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> bool:
        """Handle the tool_use/tool_result blocks the protocol delivers in
        user-type envelopes.

        Returns True when any result was terminal work. ``content`` may also
        be a plain prompt string, which carries neither.
        """
        if not isinstance(message.content, list):
            return False
        replied_this_turn = False
        for block in message.content:
            replied_this_turn |= await self._dispatch_tool_block(
                block, pending_tool_names, room_id, tools
            )
        return replied_this_turn

    async def _dispatch_tool_block(
        self,
        block: Any,
        pending_tool_names: dict[str, str],
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> bool:
        """Handle one ToolUseBlock/ToolResultBlock entry, shared by assistant-
        and user-envelope message handling; any other block type is a no-op.
        Returns True when the block was terminal work (a tool result)."""
        match block:
            case ToolUseBlock():
                await self._on_tool_use(block, pending_tool_names, room_id, tools)
                return False
            case ToolResultBlock():
                return await self._on_tool_result(
                    block, pending_tool_names, room_id, tools
                )
            case _:
                return False

    async def _send_narration_event(
        self,
        tools: AgentToolsProtocol,
        *,
        gate: Emit,
        content: str | Callable[[], str],
        message_type: str,
    ) -> None:
        """Best-effort room event, gated on the adapter's declared Emit features.

        Shared by every observability event (thought/tool_call/tool_result) —
        the session-id task event is not opt-in bookkeeping, so it posts
        unconditionally via its own path (_persist_session_id).

        ``content`` may be a callable so payload serialization only happens
        past the gate and inside this try — a large or unserializable tool
        payload costs nothing when narration is off and can't abort the turn.
        """
        if gate not in self.features.emit:
            return
        try:
            await tools.send_event(
                content=content() if callable(content) else content,
                message_type=message_type,
            )
        except Exception as e:
            logger.warning("Failed to send %s event: %s", message_type, e)

    async def _narrate_thinking(
        self, block: ThinkingBlock, room_id: str, tools: AgentToolsProtocol
    ) -> None:
        """Log a thinking block and post it as a thought event when enabled."""
        logger.debug("Room %s: Thinking: %s...", room_id, block.thinking[:100])
        await self._send_narration_event(
            tools, gate=Emit.THOUGHTS, content=block.thinking, message_type="thought"
        )

    async def _on_tool_use(
        self,
        block: ToolUseBlock,
        pending_tool_names: dict[str, str],
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Register a pending call (for the terminal-work check at turn end)
        and narrate it. Shared by both envelopes a call can arrive in — the
        protocol's assistant messages, and user messages when the call is
        carried by a subagent/nested tool_use block."""
        # Bare name for the cross-adapter tool_call record (the SDK
        # namespaces our tools mcp__band__*; see _semantic_tool_name).
        tool_name = self._semantic_tool_name(block.name)
        pending_tool_names[block.id] = tool_name
        await self._narrate_tool_call(block, tool_name, room_id, tools)

    async def _narrate_tool_call(
        self,
        block: ToolUseBlock,
        tool_name: str,
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Log a tool call and post it as a tool_call event when enabled."""
        logger.info(
            "Room %s: Tool call: %s with %s...",
            room_id,
            tool_name,
            str(block.input)[:100],
        )
        await self._send_narration_event(
            tools,
            gate=Emit.TOOL_CALLS,
            content=lambda: json.dumps(
                {
                    ToolEventKey.NAME: tool_name,
                    ToolEventKey.ARGS: block.input,
                    ToolEventKey.TOOL_CALL_ID: block.id,
                }
            ),
            message_type="tool_call",
        )

    async def _on_turn_complete(
        self,
        sdk_message: ResultMessage,
        room_id: str,
        tools: AgentToolsProtocol,
        *,
        replied_this_turn: bool,
    ) -> None:
        """Close out the turn: persist session id, emit usage, surface failure."""
        logger.info(
            "Room %s: Complete - %sms, $%.4f",
            room_id,
            sdk_message.duration_ms,
            sdk_message.total_cost_usd or 0,
        )
        if sdk_message.session_id:
            await self._persist_session_id(sdk_message.session_id, room_id, tools)
        # The ResultMessage carries the turn's total usage (the SDK runs
        # its own tool loop internally, so this is already aggregated).
        await self.emit_usage(tools, self._usage_from_result(sdk_message))
        # Consumed exactly once per turn regardless of outcome, so a decline
        # that never explains a silence (the turn replied anyway, or errored
        # outright) doesn't linger and grow this room's entry unbounded.
        notified = self._notified_declines.pop(room_id, None)
        if sdk_message.is_error:
            await self._report_error(tools, self._result_error_detail(sdk_message))
        elif not replied_this_turn and not self._declined_the_reply(
            sdk_message.permission_denials, notified
        ):
            await self._report_error(tools, missing_reply_error("Claude SDK"))

    def _declined_the_reply(
        self, permission_denials: list[Any] | None, notified: set[str] | None
    ) -> bool:
        """Whether this turn's silence is already explained by a decline.

        ``permission_denials`` is ``ResultMessage.permission_denials`` — the
        CLI's own record of every tool denied during this turn (verified
        live: it never carries a denial from a different turn), each with the
        ``tool_name`` and ``tool_use_id`` it denied. ``notified`` is the one
        thing the CLI can't tell us — which of those denials actually reached
        the room as a decline notice (see _record_notified_decline). A denial
        only explains the silence when it was both notified and the declined
        tool is what would have delivered the reply (is_terminal_success) —
        a declined side tool like Bash still leaves the turn's question
        unanswered.
        """
        if not notified:
            return False
        for denial in permission_denials or []:
            if not isinstance(denial, dict):
                continue
            raw_tool_name = denial.get("tool_name")
            if denial.get("tool_use_id") not in notified or not isinstance(
                raw_tool_name, str
            ):
                continue
            # The CLI reports the tool_use block's own name, which for an MCP
            # tool is namespaced (see _semantic_tool_name) — strip it before
            # comparing, same as every other tool-name check in this adapter.
            tool_name = self._semantic_tool_name(raw_tool_name)
            if is_terminal_success(
                tool_name,
                succeeded=True,
                custom_terminal=tool_name in self._custom_terminal_names,
            ):
                return True
        return False

    def _record_notified_decline(self, room_id: str, tool_use_id: str) -> None:
        """Record that a decline notice for ``tool_use_id`` reached the room.

        Single source of truth for the three call sites that can determine a
        decline was actually delivered (auto_decline, manual /decline, manual
        timeout) — deliberately not called for a forced resolution (approval
        eviction, room teardown; see _FORCED_DECLINE), which never posts a
        notice for the specific call it force-declines.
        """
        self._notified_declines.setdefault(room_id, set()).add(tool_use_id)

    async def _persist_session_id(
        self, session_id: str, room_id: str, tools: AgentToolsProtocol
    ) -> None:
        """Cache the session id for resume and, when it changed, persist it as
        a task event (best-effort)."""
        prev_session_id = self._session_ids.get(room_id)
        self._session_ids[room_id] = session_id
        logger.debug("Room %s: Captured session_id %s", room_id, session_id)
        if session_id == prev_session_id:
            return
        try:
            await tools.send_event(
                content="Claude SDK session",
                message_type="task",
                metadata={SESSION_ID_METADATA_KEY: session_id},
            )
        except Exception as e:
            logger.warning("Room %s: Failed to persist session_id: %s", room_id, e)

    async def _on_tool_result(
        self,
        block: ToolResultBlock,
        pending_tool_names: dict[str, str],
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> bool:
        """Narrate one tool result and report whether it was terminal work.

        Shared by both envelopes a result can arrive in: user-type messages
        (the protocol shape) and assistant messages (accepted defensively).
        Returns True when the finished call counts as the turn's productive
        work (see is_terminal_success) — i.e. the agent already answered.
        """
        result_tool_name = pending_tool_names.pop(block.tool_use_id, None)
        await self._narrate_tool_result(block, result_tool_name, room_id, tools)
        return self._tool_result_is_terminal(block, result_tool_name)

    async def _narrate_tool_result(
        self,
        block: ToolResultBlock,
        result_tool_name: str | None,
        room_id: str,
        tools: AgentToolsProtocol,
    ) -> None:
        """Log a tool result and post it as a tool_result event when enabled."""
        logger.debug(
            "Room %s: Tool result: %s... error=%s",
            room_id,
            block.tool_use_id[:20],
            block.is_error,
        )
        # NAME and IS_ERROR are required by parse_tool_result (parsing.py):
        # without a name it drops the event outright, and every sibling
        # adapter's tool_result payload sets both.
        await self._send_narration_event(
            tools,
            gate=Emit.TOOL_CALLS,
            content=lambda: json.dumps(
                {
                    ToolEventKey.NAME: result_tool_name,
                    ToolEventKey.OUTPUT: _redact_image_data(block.content),
                    ToolEventKey.TOOL_CALL_ID: block.tool_use_id,
                    ToolEventKey.IS_ERROR: block.is_error,
                }
            ),
            message_type="tool_result",
        )

    def _tool_result_is_terminal(
        self, block: ToolResultBlock, result_tool_name: str | None
    ) -> bool:
        """Whether this finished call counts as the turn's productive work."""
        # Belt and braces with the sibling adapters: a Band tool wrapper that
        # caught an exception returns an "Error " string without is_error, so
        # cross-check the content too (see band_tool_errored).
        return is_terminal_success(
            result_tool_name,
            succeeded=not block.is_error
            and not band_tool_errored(result_tool_name, block.content),
            custom_terminal=result_tool_name in self._custom_terminal_names,
        )

    @staticmethod
    def _stream_ended_without_result_error() -> str:
        """Room-visible detail when the CLI stream ends without a ResultMessage.

        Distinct from ``_result_error_detail`` (a completed turn whose
        ResultMessage reports failure) and ``missing_reply_error`` (a completed
        turn that never called a reply tool): this turn never reached a
        terminal message at all, e.g. the CLI subprocess exited or its stdout
        closed mid-turn.
        """
        return (
            "Claude SDK turn ended without a result: the CLI process exited "
            "or its output stream closed before this turn completed."
        )

    @staticmethod
    def _result_error_detail(sdk_message: ResultMessage) -> str:
        """Room-visible detail for a turn where ``ResultMessage.is_error`` is set."""
        detail = (
            sdk_message.result
            or "; ".join(sdk_message.errors or [])
            or ("no error detail provided by the Claude CLI")
        )
        if sdk_message.api_error_status:
            detail = f"{detail} (API status {sdk_message.api_error_status})"
        return f"Claude SDK turn failed: {detail}"

    @staticmethod
    def _usage_from_result(sdk_message: ResultMessage) -> TurnUsage:
        """Map a Claude SDK ``ResultMessage.usage`` (raw API dict) onto TurnUsage.

        Raw per the TurnUsage convention: the Claude API's ``input_tokens``
        excludes cached tokens (reported separately in the cache fields).
        ``usage`` may be absent; ``from_mapping`` yields empty usage then.
        """
        return TurnUsage.from_mapping(
            getattr(sdk_message, "usage", None),
            input="input_tokens",
            output="output_tokens",
            cache_read="cache_read_input_tokens",
            cache_write="cache_creation_input_tokens",
        )

    # --- Copied from BandClaudeSDKAgent._cleanup_session ---
    async def on_cleanup(self, room_id: str) -> None:
        """Clean up Claude SDK session and stored tools when agent leaves a room."""
        self._clear_pending_approvals_for_room(room_id)
        if self._session_manager:
            await self._session_manager.cleanup_session(room_id)
        self._room_tools.pop(room_id, None)
        self._session_context.pop(room_id, None)
        self._session_ids.pop(room_id, None)
        self._room_last_sender.pop(room_id, None)
        self._notified_declines.pop(room_id, None)
        self._pending_tool_names.pop(room_id, None)
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
        self._notified_declines.clear()
        self._pending_tool_names.clear()

    # ------------------------------------------------------------------
    # Chat-based approval flow
    # ------------------------------------------------------------------

    @staticmethod
    def _semantic_tool_name(sdk_tool_name: str) -> str:
        """The bare tool name for platform/user-facing records.

        claude_sdk exposes band + custom tools through an in-process MCP server, so
        the Claude Agent SDK namespaces them as ``mcp__band__<tool>``. The platform
        ``tool_call`` event and the approval UX are cross-adapter, semantic records
        where every other adapter uses the bare name, so strip our own server's
        prefix (``MCP_TOOL_PREFIX``). Any external MCP server's tools stay namespaced.
        """
        return sdk_tool_name.removeprefix(MCP_TOOL_PREFIX)

    def _make_can_use_tool(self, room_id: str) -> CanUseTool:
        """Return a room-bound ``can_use_tool`` callback for the Claude SDK."""

        async def _can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            return await self._resolve_tool_permission(
                room_id, tool_name, tool_input, context
            )

        return _can_use_tool

    async def _resolve_tool_permission(
        self,
        room_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Decide allow/deny for one tool call under the room's approval mode."""
        # The SDK passes the MCP-namespaced name; use the bare name everywhere
        # this approval surfaces to the user (summary, notifications, logs).
        tool_name = self._semantic_tool_name(tool_name)
        summary = self._approval_summary(tool_name, tool_input)
        # Capture the sender now so it doesn't get overwritten by
        # messages arriving while we wait for a decision.
        requester = self._room_last_sender.get(room_id)
        # Always a real string for a can_use_tool callback (see
        # ToolPermissionContext.tool_use_id); matched against this same call's
        # entry in ResultMessage.permission_denials at turn end.
        tool_use_id = context.tool_use_id
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
            notified = False
            if self.approval_text_notifications:
                notified = await self._notify_auto_decision(
                    room_id, summary, "decline", requester=requester
                )
            if notified and tool_use_id:
                self._record_notified_decline(room_id, tool_use_id)
            return PermissionResultDeny(
                message=f"Tool use declined by policy: {summary}"
            )

        # --- manual mode ---------------------------------------------------
        return await self._resolve_manual_approval(
            room_id, tool_name, tool_input, summary, tool_use_id, requester=requester
        )

    async def _send_best_effort(
        self,
        tools: AgentToolsProtocol,
        message: str,
        mentions: list[str] | None,
        *,
        room_id: str,
        failure_note: str,
        log_level: int = logging.WARNING,
    ) -> bool:
        """Send ``message``, returning whether it was actually delivered.

        Swallows the send failure -- callers use the returned bool to decide
        whether a missing-reply guard still applies.
        """
        try:
            await tools.send_message(message, mentions=mentions)
            return True
        except Exception as e:
            logger.log(log_level, "Room %s: %s: %s", room_id, failure_note, e)
            return False

    async def _notify_auto_decision(
        self,
        room_id: str,
        summary: str,
        decision: str,
        *,
        requester: dict[str, str] | None = None,
    ) -> bool:
        """Best-effort chat notification for auto-approve / auto-decline.

        Returns whether the notice actually reached the room. A silent
        auto_decline (no room binding, or the send itself fails) still needs
        the missing-reply guard at turn end — only a delivered notice already
        explains the turn's silence.
        """
        tools = self._room_tools.get(room_id)
        if not tools:
            return False
        mention = [requester["id"]] if requester else None
        return await self._send_best_effort(
            tools,
            f"Approval requested ({summary}). Policy decision: **{decision}**.",
            mention,
            room_id=room_id,
            failure_note="Failed to send approval policy notification",
        )

    async def _resolve_manual_approval(
        self,
        room_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        summary: str,
        tool_use_id: str | None,
        *,
        requester: dict[str, str] | None = None,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Block until a user approves / declines via ``/approve`` or ``/decline``."""
        loop = asyncio.get_running_loop()
        token = self._next_approval_token(room_id)

        pending = PendingApproval(
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
                oldest.future.set_result(_FORCED_DECLINE)
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
                # No notice reached the room — the one delivery attempt is
                # the failure itself — so this must not suppress the
                # missing-reply guard the way the other decline paths below
                # (which do post a notice) correctly do.
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
            # Only a genuine "decline" (a human replying to the approval
            # prompt via _handle_approval_command, which posts its own
            # resolved-as-decline notice) implies delivery. A forced
            # resolution — eviction or room teardown, _FORCED_DECLINE — never
            # posts a notice for this specific call, so must not count.
            if decision_raw == "decline" and tool_use_id:
                self._record_notified_decline(room_id, tool_use_id)
            return PermissionResultDeny(message="User declined tool use")

        except asyncio.TimeoutError:
            decision: ApprovalDecision = self.approval_timeout_decision
            notified = False
            if tools:
                notified = await self._send_best_effort(
                    tools,
                    f"Approval `{token}` timed out. Decision: **{decision}**.",
                    mention,
                    room_id=room_id,
                    failure_note="Failed to send timeout notification",
                    log_level=logging.DEBUG,
                )

            if decision == "accept":
                return PermissionResultAllow()
            # Suppressing the missing-reply guard requires a delivered notice:
            # a timeout nobody heard about must still surface as an error.
            if notified and tool_use_id:
                self._record_notified_decline(room_id, tool_use_id)
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
        natural language like ``"don't /decline it"``. The platform prepends an
        ``@handle`` mention block to a delivered room reply (a reply must mention
        the agent to arrive), so that block is stripped first -- otherwise the
        command never leads the content and ``/approve``/``/decline`` silently
        miss. Returns ``(command, rest)`` or ``None``.
        """
        stripped = strip_leading_mentions(content).lstrip()
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
        selected: PendingApproval | None = None

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
        notified = await self._send_best_effort(
            tools,
            f"Approval `{token}` resolved as **{decision}**.",
            mention,
            room_id=room_id,
            failure_note=f"Failed to send approval resolution notice for token {token}",
        )

        if not selected.future.done():
            # A failed notice for a decline must not claim delivery --
            # _FORCED_DECLINE is the existing "declined with no notice"
            # sentinel (matches eviction/teardown above), which
            # _resolve_manual_approval's decision_raw == "decline" check
            # correctly treats as not implying the missing-reply guard is
            # covered. An accept has no such guard to protect, so it always
            # resolves as a genuine accept regardless of notice delivery.
            resolved = (
                decision if (notified or decision == "accept") else _FORCED_DECLINE
            )
            selected.future.set_result(resolved)

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
                item.future.set_result(_FORCED_DECLINE)
        # Keep the seq counter to avoid token collisions with suspended coroutines
