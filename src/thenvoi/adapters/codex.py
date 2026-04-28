"""Codex app-server adapter."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time as _time
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Any, Callable, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from thenvoi.converters.codex import CodexHistoryConverter
from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import (
    AdapterFeatures,
    AgentInput,
    Capability,
    Emit,
    PlatformMessage,
)
from thenvoi.integrations.codex import (
    CodexJsonRpcError,
    CodexStdioClient,
    CodexWebSocketClient,
    RpcEvent,
)
from thenvoi.integrations.codex.types import (
    CODEX_APPROVAL_METHODS,
    ApprovalAuditEntry,
    CodexSessionState,
    CodexTokenUsage,
    build_structured_error_metadata,
    parse_plan_steps,
)
from thenvoi.runtime.custom_tools import (
    CustomToolDef,
    custom_tool_to_openai_schema,
    execute_custom_tool,
    find_custom_tool,
)
from thenvoi.runtime.prompts import render_system_prompt

logger = logging.getLogger(__name__)

TransportKind = Literal["stdio", "ws", "sdk"]
ApprovalMode = Literal["auto_accept", "auto_decline", "manual"]
ApprovalDecision = Literal["accept", "acceptForSession", "decline"]
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
_REASONING_SUMMARIES = {"auto", "concise", "detailed", "none"}

# Platform tools whose execution should not be reported as tool_call/tool_result
# events — they already produce visible output (messages or events) on the platform.
_SILENT_REPORTING_TOOLS: frozenset[str] = frozenset(
    {
        "thenvoi_send_message",
        "thenvoi_send_event",
        "setmodel",
        "setreasoning",
    }
)

# Slash commands recognised by _extract_local_command().
_LOCAL_COMMANDS: frozenset[str] = frozenset(
    {
        "help",
        "status",
        "model",
        "models",
        "reasoning",
        "approvals",
        "approve",
        "approve-session",
        "decline",
        "sandbox",
        "permissions",
        "threads",
        "thread",
        "usage",
    }
)

# How many tokens from the start of the message to scan for a ``/command``.
# The platform prepends mentions as ``@handle DisplayName`` pairs (two tokens
# per participant).  20 tokens comfortably covers up to ~10 concurrent
# mentions, which is the realistic upper bound for a room, while stopping
# well short of the message body so a slash word used as prose
# (e.g. "use /tmp as …") remains prose.
_COMMAND_TOKEN_SEARCH_LIMIT = 20

# Upper bound on cached task titles (room-lifecycle map used to preserve the
# title between task_started and task_complete events).  500 covers bursty
# conversations while keeping memory bounded.
_MAX_TASK_TITLES = 500

# Cap on the raw diff string forwarded in ``turn/diff/updated`` task metadata,
# expressed in UTF-8 bytes.  Diffs can be megabytes on large refactors;
# shipping the full blob inflates WebSocket frames and chat-event storage.
# Consumers that need the full diff should request it from Codex directly.
# Enforcing the cap on encoded bytes (not Python characters) bounds the wire
# size uniformly regardless of whether the diff contains ASCII or heavy
# multi-byte content (emoji, CJK).
_MAX_DIFF_METADATA_BYTES = 64 * 1024


# ---------------------------------------------------------------------------
# Self-configuration tools — let Codex change its own model/reasoning at runtime
# ---------------------------------------------------------------------------


class SetModelInput(BaseModel):
    """Switch the model used for subsequent turns. Call this when a different model would be more appropriate for the task (e.g. a faster model for simple queries, a stronger model for complex reasoning)."""

    model: str = Field(description="Model ID to use (e.g. 'gpt-5.3-codex', 'gpt-5.2').")


class SetReasoningInput(BaseModel):
    """Adjust reasoning effort and summary detail for subsequent turns. Use higher effort for complex problems and lower effort for straightforward tasks."""

    effort: str | None = Field(
        default=None,
        description="Reasoning effort level: none, minimal, low, medium, high, or xhigh. Omit to keep current.",
    )
    summary: str | None = Field(
        default=None,
        description="Reasoning summary detail: auto, concise, detailed, or none. Omit to keep current.",
    )


# Hardcoded default — update when OpenAI rotates model IDs.
# Override at runtime via CodexAdapterConfig.model or CODEX_MODEL env var.
_DEFAULT_MODEL = "gpt-5.3-codex"


class _CodexClientProtocol(Protocol):
    async def connect(self) -> None: ...

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]: ...

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent: ...

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None: ...

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


@dataclass
class _PendingApproval:
    request_id: int | str
    method: str
    summary: str
    created_at: datetime
    future: asyncio.Future[str]
    session_key: str = ""


@dataclass
class _TurnResult:
    """Aggregated result from processing a single Codex turn's event stream."""

    final_text: str = ""
    turn_status: str = "failed"
    turn_error: str = ""
    saw_send_message_tool: bool = False


@dataclass
class CodexAdapterConfig:
    """Runtime configuration for Codex adapter sessions.

    Notes on ``transport``:
        - ``"stdio"`` and ``"ws"`` work on all supported Python versions.
        - ``"sdk"`` requires the optional ``codex-app-server`` dependency,
          which only publishes wheels for Python >= 3.12.  On 3.11 and
          below, ``pip install thenvoi-sdk[codex]`` silently omits it and
          constructing an ``"sdk"``-transport adapter raises ``ImportError``
          at ``on_started()``.  Use ``"stdio"`` / ``"ws"`` on older
          interpreters.

    Turn task events:
        ``emit_turn_task_markers`` and ``emit_turn_lifecycle_events`` are
        independent channels.  Enabling both will produce **two** task
        events per completed turn: one "Codex turn" marker and one
        enriched "Codex turn lifecycle" event (plus a "started" lifecycle
        event at turn start).  Consumers that render task timelines
        should pick exactly one channel — typically the lifecycle channel
        when its extra metadata is desired.
    """

    transport: TransportKind = "stdio"
    model: str | None = None
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    ) = None
    reasoning_summary: Literal["auto", "concise", "detailed", "none"] | None = None
    cwd: str | None = None
    approval_policy: str = "never"
    personality: Literal["friendly", "pragmatic", "none"] = "pragmatic"
    sandbox: str | None = None
    sandbox_policy: dict[str, Any] | None = None
    system_prompt: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = True
    experimental_api: bool = True
    enable_task_events: bool = True
    emit_turn_task_markers: bool = False
    emit_thought_events: bool = False
    fallback_send_agent_text: bool = True
    approval_mode: ApprovalMode = "manual"
    approval_text_notifications: bool = True
    approval_wait_timeout_s: float = 300.0
    approval_timeout_decision: ApprovalDecision = "decline"
    turn_timeout_s: float = 180.0
    client_name: str = "thenvoi_codex_adapter"
    client_title: str = "Thenvoi Codex Adapter"
    client_version: str = "0.1.0"
    codex_command: tuple[str, ...] | None = None
    codex_env: dict[str, str] | None = None
    codex_ws_url: str = "ws://127.0.0.1:8765"
    enable_execution_reporting: bool = False
    enable_self_config_tools: bool = False
    additional_dynamic_tools: list[dict[str, Any]] = field(default_factory=list)
    inject_history_on_resume_failure: bool = True
    max_history_messages: int = 50
    # Fallback models tried when model/list fails or returns empty.
    # Update when OpenAI rotates model IDs.
    fallback_models: tuple[str, ...] = ("gpt-5.2", "gpt-5.3-codex")
    max_pending_approvals_per_room: int = 50
    max_approval_audit_per_room: int = 100
    # Upper bound on session-level approvals retained per room
    # (``/approve-session`` patterns).  Evicted LRU-style when exceeded so a
    # long-lived room that approves many distinct commands can't grow this
    # set without bound.
    max_session_approved_per_room: int = 100
    # Upper bound for the transport client's close() during on_cleanup so
    # _rpc_lock can't be held indefinitely if the underlying subprocess or
    # socket is unresponsive.  ``None`` disables the bound (legacy behavior).
    client_close_timeout_s: float | None = 10.0
    # Matching rules for session-level auto-approval (``/approve-session``).
    #
    # ``"full_command"`` (default, more restrictive) — the key is the exact
    #     command string after ``.strip()`` on the single ``command`` field
    #     of the approval payload.  Matching is byte-exact and case-sensitive;
    #     only leading/trailing whitespace is normalised.  ``npm test`` does
    #     NOT match ``npm  test`` (double space), ``NPM TEST`` (case), or
    #     ``npm test --watch`` (trailing args).
    #
    # ``"binary"`` (less restrictive) — the key is just the first whitespace-
    #     delimited token of the command.  ``/approve-session`` on ``npm test``
    #     auto-approves every future command starting with ``npm`` in that
    #     room, regardless of subcommand or arguments.  Intended for
    #     trusted-binary shortcuts (``npm``, ``git``) where per-argv friction
    #     outweighs the safety margin.  Not recommended for commands that can
    #     take destructive flags (``rm``, ``git push --force``).
    #
    # File-change approvals always key on the sorted set of paths being
    # modified, independent of this flag.
    session_approval_granularity: Literal["binary", "full_command"] = "full_command"
    # --- Phase 1: Structured errors & enriched approvals ---
    structured_errors: bool = True
    # --- Phase 2: Plan & task lifecycle ---
    stream_plan_events: bool = False
    emit_turn_lifecycle_events: bool = False
    # --- Phase 3: Real-time streaming ---
    stream_reasoning_events: bool = False
    stream_commentary_events: bool = False
    # --- Phase 4: Diffs & token usage ---
    emit_diff_events: bool = False
    emit_token_usage_events: bool = False


class CodexAdapter(SimpleAdapter[CodexSessionState]):
    """
    Codex adapter backed by codex app-server (stdio or websocket transport).

    One Thenvoi room maps to one Codex thread. Mapping is persisted in task
    events metadata and restored via CodexHistoryConverter on bootstrap.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        config: CodexAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        history_converter: CodexHistoryConverter | None = None,
        client_factory: Callable[[CodexAdapterConfig], _CodexClientProtocol]
        | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        self._config = config or CodexAdapterConfig()

        # --- Deprecation shim: boolean → features migration ---
        # Only trigger for non-default booleans (enable_task_events defaults
        # to True, so it doesn't count as "legacy usage").
        _has_legacy_booleans = (
            self._config.enable_execution_reporting or self._config.emit_thought_events
        )
        if _has_legacy_booleans and features is not None:
            raise ThenvoiConfigError(
                "Cannot pass both legacy boolean flags in CodexAdapterConfig "
                "(enable_execution_reporting / emit_thought_events) "
                "and 'features'. "
                "Use features=AdapterFeatures(...) instead."
            )

        # Build features from config booleans when not explicitly provided.
        if features is None:
            if _has_legacy_booleans:
                warnings.warn(
                    "enable_execution_reporting and emit_thought_events in "
                    "CodexAdapterConfig are deprecated. "
                    "Use features=AdapterFeatures(emit={Emit.EXECUTION, "
                    "Emit.THOUGHTS}) instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            emit: frozenset[Emit] = frozenset()
            if self._config.enable_execution_reporting:
                emit = emit | frozenset({Emit.EXECUTION})
            if self._config.emit_thought_events:
                emit = emit | frozenset({Emit.THOUGHTS})
            if self._config.enable_task_events:
                emit = emit | frozenset({Emit.TASK_EVENTS})
            features = AdapterFeatures(capabilities=frozenset(), emit=emit)

        super().__init__(
            history_converter=history_converter or CodexHistoryConverter(),
            features=features,
        )
        self.config = self._config
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        if self.config.enable_self_config_tools:
            self._custom_tools.extend(self._build_self_config_tools())
        self._client_factory = client_factory
        self._client: _CodexClientProtocol | None = None
        self._initialized = False
        self._selected_model: str | None = None
        self._model_explicitly_set: bool = bool(self.config.model)
        self._system_prompt: str = ""
        self._room_threads: dict[str, str] = {}
        self._prompt_injected_rooms: set[str] = set()
        self._task_titles_by_id: OrderedDict[str, str] = OrderedDict()
        self._max_task_titles: int = _MAX_TASK_TITLES
        self._pending_approvals: dict[str, dict[str, _PendingApproval]] = {}
        self._raw_history_by_room: dict[str, list[dict[str, Any]]] = {}
        self._needs_history_injection: set[str] = set()
        # Token usage tracking per thread
        self._token_usage: dict[str, CodexTokenUsage] = {}
        # Approval audit trail per room
        self._approval_audit: dict[str, list[ApprovalAuditEntry]] = {}
        # Session-level approved patterns.  OrderedDict (used as an ordered
        # set) so insertion order is preserved for LRU eviction when a room
        # hits ``max_session_approved_per_room``.  Value is unused.
        self._session_approved: dict[str, OrderedDict[str, None]] = {}
        # Per-room sandbox overrides (set via /sandbox command)
        self._sandbox_overrides: dict[str, str] = {}
        # SDK transport: context for async server-request handling.
        # Only set via _sdk_request_scope() context manager which guarantees
        # cleanup.  Must only be entered while _rpc_lock is held.
        self._sdk_request_context: (
            tuple[AgentToolsProtocol, PlatformMessage, str] | None
        ) = None
        # Single client receive queue means turn processing must be serialized
        # — the lock is adapter-wide, not per-room.  A pending manual approval
        # in room A therefore blocks turn processing in every other room for
        # up to ``approval_wait_timeout_s`` (300s default). Approval resolution
        # commands (/approve, /decline) are handled *outside* this lock in
        # ``on_message`` so they can unblock a waiting turn.
        self._rpc_lock = asyncio.Lock()

    def _build_self_config_tools(self) -> list[CustomToolDef]:
        """Build custom tools that let Codex change its own model/reasoning.

        Note: ``_handle_set_model`` and ``_handle_set_reasoning`` closures
        mutate adapter state (``config.model``, ``_selected_model``, etc.).
        They are safe because they are always called inside the
        ``_handle_server_request`` path which holds ``_rpc_lock``.
        """
        adapter = self

        def _handle_set_model(inp: SetModelInput) -> str:
            if not adapter._rpc_lock.locked():
                raise RuntimeError("_handle_set_model must run under _rpc_lock")
            adapter.config.model = inp.model
            adapter._selected_model = inp.model
            adapter._model_explicitly_set = True
            return f"Model changed to {inp.model} for subsequent turns."

        def _handle_set_reasoning(inp: SetReasoningInput) -> str:
            if not adapter._rpc_lock.locked():
                raise RuntimeError("_handle_set_reasoning must run under _rpc_lock")
            parts: list[str] = []
            if inp.effort is not None:
                if inp.effort not in _REASONING_EFFORTS:
                    return (
                        f"Invalid reasoning effort '{inp.effort}'. "
                        f"Valid: {', '.join(sorted(_REASONING_EFFORTS))}."
                    )
                adapter.config.reasoning_effort = inp.effort  # type: ignore[assignment]  # Literal narrowed by Pydantic validation
                parts.append(f"effort={inp.effort}")
            if inp.summary is not None:
                if inp.summary not in _REASONING_SUMMARIES:
                    return (
                        f"Invalid reasoning summary '{inp.summary}'. "
                        f"Valid: {', '.join(sorted(_REASONING_SUMMARIES))}."
                    )
                adapter.config.reasoning_summary = inp.summary  # type: ignore[assignment]  # Literal narrowed by Pydantic validation
                parts.append(f"summary={inp.summary}")
            if not parts:
                return (
                    f"No changes. Current: effort={adapter.config.reasoning_effort or 'default'}, "
                    f"summary={adapter.config.reasoning_summary or 'default'}."
                )
            return f"Reasoning updated: {', '.join(parts)}."

        return [
            (SetModelInput, _handle_set_model),
            (SetReasoningInput, _handle_set_reasoning),
        ]

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        self._build_system_prompt()
        async with self._rpc_lock:
            await self._ensure_client_ready()
        self._log_startup_config(agent_name)

    def _log_startup_config(self, agent_name: str) -> None:
        # The two turn-task channels are independent by design but rendering
        # both produces duplicated timeline entries in most UIs.  Warn once
        # at startup so operators catch the misconfiguration instead of
        # discovering it as "twice as many events" downstream.
        if (
            self.config.emit_turn_task_markers
            and self.config.emit_turn_lifecycle_events
            and Emit.TASK_EVENTS in self.features.emit
        ):
            logger.warning(
                "Codex adapter: both emit_turn_task_markers and "
                "emit_turn_lifecycle_events are enabled — consumers will "
                "receive two task events per turn.  Pick exactly one channel "
                "(typically emit_turn_lifecycle_events for the richer metadata)."
            )
        logger.info(
            "Codex adapter started: agent=%s, transport=%s, model=%s, "
            "sandbox=%s, approval_mode=%s, "
            "execution_reporting=%s, self_config_tools=%s, "
            "task_events=%s, turn_markers=%s, thought_events=%s, "
            "stream_reasoning=%s, stream_plan=%s, stream_commentary=%s, "
            "diffs=%s, token_usage=%s, structured_errors=%s",
            agent_name,
            self.config.transport,
            self._selected_model or self.config.model or "auto",
            self.config.sandbox or "default",
            self.config.approval_mode,
            Emit.EXECUTION in self.features.emit,
            self.config.enable_self_config_tools,
            Emit.TASK_EVENTS in self.features.emit,
            self.config.emit_turn_task_markers,
            Emit.THOUGHTS in self.features.emit,
            self.config.stream_reasoning_events,
            self.config.stream_plan_events,
            self.config.stream_commentary_events,
            self.config.emit_diff_events,
            self.config.emit_token_usage_events,
            self.config.structured_errors,
        )

    async def on_event(self, inp: AgentInput) -> None:
        if (
            self.config.inject_history_on_resume_failure
            and inp.is_session_bootstrap
            and inp.history.raw
        ):
            self._raw_history_by_room[inp.room_id] = inp.history.raw
        await super().on_event(inp)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        command = self._extract_local_command(msg.content)
        if command is not None and command[0] in {
            "approve",
            "approve-session",
            "decline",
            "approvals",
        }:
            handled = await self._handle_approval_command(
                tools=tools,
                msg=msg,
                room_id=room_id,
                command=command[0],
                args=command[1],
            )
            if handled:
                return

        async with self._rpc_lock:
            await self._ensure_client_ready()
            if self._client is None:
                raise RuntimeError(
                    "Codex client not initialized after _ensure_client_ready"
                )

            if command is not None:
                handled = await self._handle_local_command(
                    tools=tools,
                    msg=msg,
                    history=history,
                    room_id=room_id,
                    command=command[0],
                    args=command[1],
                )
                if handled:
                    return

            thread_id = await self._ensure_thread(
                room_id=room_id,
                history=history,
                tools=tools,
                is_session_bootstrap=is_session_bootstrap,
            )

            turn_input, has_pending_prompt_injection = self._build_turn_input(
                msg=msg,
                participants_msg=participants_msg,
                contacts_msg=contacts_msg,
                room_id=room_id,
            )

            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": turn_input,
            }
            self._apply_turn_overrides(turn_params, room_id=room_id)

            turn_started = await self._start_turn_with_model_fallback(turn_params)
            if has_pending_prompt_injection:
                self._prompt_injected_rooms.add(room_id)
            turn = turn_started.get("turn") if isinstance(turn_started, dict) else {}
            turn_id = str((turn or {}).get("id") or "")

            if (
                Emit.TASK_EVENTS in self.features.emit
                and self.config.emit_turn_task_markers
            ):
                await tools.send_event(
                    content=self._build_task_event_content(
                        task_id=turn_id or None,
                        task="Codex turn",
                        status="started",
                        summary=f"Thread: {thread_id}",
                    ),
                    message_type="task",
                    metadata={
                        "codex_thread_id": thread_id,
                        "codex_turn_id": turn_id or None,
                        "codex_room_id": room_id,
                    },
                )

            # Phase 2: Turn STARTED lifecycle event with input summary
            if (
                self.config.emit_turn_lifecycle_events
                and Emit.TASK_EVENTS in self.features.emit
            ):
                input_summary = (msg.content or "")[:200]
                try:
                    await tools.send_event(
                        content=self._build_task_event_content(
                            task_id=turn_id or None,
                            task="Codex turn lifecycle",
                            status="started",
                            summary=f"Thread: {thread_id}",
                        ),
                        message_type="task",
                        metadata={
                            "codex_event_type": "turn_lifecycle",
                            "codex_room_id": room_id,
                            "codex_thread_id": thread_id,
                            "codex_turn_id": turn_id or None,
                            "codex_turn_status": "started",
                            "codex_input_summary": input_summary,
                        },
                    )
                except Exception:
                    logger.debug(
                        "Failed to emit turn started lifecycle event",
                        exc_info=True,
                    )

            # Reset per-turn token deltas for the new turn.
            usage_obj = self._token_usage.get(thread_id)
            if usage_obj is not None:
                usage_obj.reset_turn_deltas()

            _turn_start = _time.monotonic()
            try:
                async with self._sdk_request_scope(tools, msg, room_id):
                    result = await self._process_turn_events(
                        tools=tools,
                        msg=msg,
                        room_id=room_id,
                        thread_id=thread_id,
                        turn_id=turn_id or None,
                        turn_start=_turn_start,
                    )
            except Exception:
                logger.exception(
                    "Unexpected error during Codex turn event processing "
                    "(thread=%s, turn=%s)",
                    thread_id,
                    turn_id,
                )
                result = _TurnResult(
                    turn_status="failed",
                    turn_error="Internal error during turn processing",
                )

            _turn_duration_s = _time.monotonic() - _turn_start
            await self._emit_turn_outcome(
                tools=tools,
                msg=msg,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=turn_id or None,
                turn_status=result.turn_status,
                turn_error=result.turn_error,
                final_text=result.final_text,
                saw_send_message_tool=result.saw_send_message_tool,
                duration_s=_turn_duration_s,
            )

    @contextlib.asynccontextmanager
    async def _sdk_request_scope(
        self,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
    ):
        """Set SDK request context for the duration of the turn event loop.

        Must only be entered while ``_rpc_lock`` is held.  The context
        manager guarantees cleanup even on exceptions, preventing stale
        context from routing server requests to the wrong room.  The
        single-slot ``_sdk_request_context`` assumes serialized turns;
        the assertion below catches any regression that introduces
        concurrent entry (e.g. a future per-room lock).
        """
        if self._sdk_request_context is not None:
            raise RuntimeError(
                "_sdk_request_scope re-entered; _rpc_lock must serialize turns"
            )
        self._sdk_request_context = (tools, msg, room_id)
        try:
            yield
        finally:
            self._sdk_request_context = None

    async def _process_turn_events(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        turn_start: float,
    ) -> _TurnResult:
        """Consume the Codex event stream for a single turn and return the result."""
        if self._client is None:
            raise RuntimeError("CodexAdapter client is None during turn event loop")

        result = _TurnResult()
        try:
            while True:
                _remaining = max(
                    0.0,
                    self.config.turn_timeout_s - (_time.monotonic() - turn_start),
                )
                event = await self._client.recv_event(timeout_s=_remaining)
                if event.kind == "request":
                    used_send_message = await self._handle_server_request(
                        tools=tools,
                        msg=msg,
                        room_id=room_id,
                        event=event,
                    )
                    result.saw_send_message_tool = (
                        result.saw_send_message_tool or used_send_message
                    )
                    continue

                params = event.params if isinstance(event.params, dict) else {}

                if event.method in {
                    "codex/event/task_started",
                    "codex/event/task_complete",
                }:
                    await self._forward_raw_task_event(
                        tools=tools,
                        room_id=room_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        method=event.method,
                        params=params,
                    )
                    continue

                if event.method == "error":
                    await self._handle_error_event(
                        tools=tools,
                        params=params,
                        room_id=room_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                    )
                    continue

                # --- Phase 3: Real-time streaming ---
                if event.method in {
                    "item/reasoning/summaryTextDelta",
                    "item/reasoning/textDelta",
                }:
                    if self.config.stream_reasoning_events:
                        delta = params.get("delta", "")
                        item_id = str(params.get("itemId") or "")
                        try:
                            await tools.send_event(
                                content=str(delta),
                                message_type="thought",
                                metadata={
                                    "streaming": True,
                                    "codex_item_id": item_id,
                                    "codex_event_type": event.method,
                                    "codex_room_id": room_id,
                                    "codex_thread_id": thread_id,
                                    "codex_turn_id": turn_id,
                                },
                            )
                        except Exception:
                            logger.debug(
                                "Failed to stream reasoning delta",
                                exc_info=True,
                            )
                    continue

                if event.method == "item/plan/delta":
                    if self.config.stream_plan_events:
                        delta = params.get("delta", "")
                        item_id = str(params.get("itemId") or "")
                        try:
                            await tools.send_event(
                                content=str(delta),
                                message_type="thought",
                                metadata={
                                    "streaming": True,
                                    "subtype": "plan",
                                    "codex_item_id": item_id,
                                    "codex_room_id": room_id,
                                    "codex_thread_id": thread_id,
                                    "codex_turn_id": turn_id,
                                },
                            )
                        except Exception:
                            logger.debug("Failed to stream plan delta", exc_info=True)
                    continue

                # --- Phase 2: Plan step tracking ---
                if event.method == "turn/plan/updated":
                    if self.config.stream_plan_events:
                        await self._forward_plan_steps(
                            tools=tools,
                            params=params,
                            room_id=room_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                        )
                    continue

                # --- Phase 4: Token usage ---
                if event.method == "thread/tokenUsage/updated":
                    self._update_token_usage(thread_id, params)
                    if self.config.emit_token_usage_events:
                        await self._emit_token_usage_event(
                            tools=tools,
                            thread_id=thread_id,
                            room_id=room_id,
                        )
                    continue

                # --- Phase 2: Context compaction events ---
                if event.method == "context/compacted":
                    if (
                        self.config.emit_turn_lifecycle_events
                        and Emit.TASK_EVENTS in self.features.emit
                    ):
                        compacted_thread = str(params.get("threadId") or thread_id)
                        compacted_turn = str(params.get("turnId") or turn_id or "")
                        try:
                            await tools.send_event(
                                content=self._build_task_event_content(
                                    task_id=compacted_turn or None,
                                    task="Codex context compaction",
                                    status="completed",
                                    summary=f"Thread: {compacted_thread}",
                                ),
                                message_type="task",
                                metadata={
                                    "codex_event_type": "context_compaction",
                                    "codex_room_id": room_id,
                                    "codex_thread_id": compacted_thread,
                                    "codex_turn_id": compacted_turn or None,
                                },
                            )
                        except Exception:
                            logger.debug(
                                "Failed to emit context compaction event",
                                exc_info=True,
                            )
                    continue

                # --- Phase 4: Aggregated diffs ---
                if event.method == "turn/diff/updated":
                    if (
                        self.config.emit_diff_events
                        and Emit.TASK_EVENTS in self.features.emit
                    ):
                        await self._forward_diff_event(
                            tools=tools,
                            params=params,
                            room_id=room_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                        )
                    continue

                if event.method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    phase = params.get("phase")
                    if isinstance(delta, str):
                        if (
                            phase == "commentary"
                            and self.config.stream_commentary_events
                        ):
                            # Stream as thought; exclude from final_text.
                            try:
                                await tools.send_event(
                                    content=delta,
                                    message_type="thought",
                                    metadata={
                                        "streaming": True,
                                        "subtype": "commentary",
                                        "codex_item_id": str(
                                            params.get("itemId") or ""
                                        ),
                                        "codex_room_id": room_id,
                                        "codex_thread_id": thread_id,
                                        "codex_turn_id": turn_id,
                                    },
                                )
                            except Exception:
                                logger.debug(
                                    "Failed to stream commentary delta",
                                    exc_info=True,
                                )
                        else:
                            # When streaming is disabled, commentary accumulates
                            # into final_text for backward compatibility.
                            result.final_text += delta
                    continue

                if event.method == "item/completed":
                    item = params.get("item") if isinstance(params, dict) else {}
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        if item_type == "agentMessage":
                            text = item.get("text")
                            if isinstance(text, str) and text:
                                result.final_text = text
                        else:
                            await self._emit_item_completed_events(
                                tools=tools,
                                item=item,
                                room_id=room_id,
                                thread_id=thread_id,
                                turn_id=turn_id,
                            )
                    continue

                if event.method == "transport/closed":
                    result.turn_status = "failed"
                    result.turn_error = "Codex transport closed unexpectedly"
                    # Reset client state so _ensure_client_ready() rebuilds
                    # on the next message instead of reusing a dead client.
                    self._client = None
                    self._initialized = False
                    # Clear per-room state that references the dead session so
                    # the next turn does a fresh thread/start instead of reusing
                    # a cached thread_id the new subprocess doesn't know about.
                    # Also drop token-usage entries keyed by the dead thread
                    # ids; otherwise they leak until the last-room teardown
                    # because on_cleanup can no longer resolve their keys.
                    stale_rooms = list(self._room_threads.keys())
                    stale_threads = list(self._room_threads.values())
                    self._room_threads.clear()
                    self._raw_history_by_room.clear()
                    for stale_thread in stale_threads:
                        self._token_usage.pop(stale_thread, None)
                    for stale_room in stale_rooms:
                        self._clear_pending_approvals_for_room(stale_room)
                    break

                if event.method == "turn/completed":
                    turn_payload = (
                        params.get("turn")
                        if isinstance(params.get("turn"), dict)
                        else {}
                    )
                    event_turn_id = str(turn_payload.get("id") or "")
                    if turn_id and event_turn_id and event_turn_id != turn_id:
                        continue
                    result.turn_status = str(turn_payload.get("status") or "failed")
                    result.turn_error = self._extract_turn_error(turn_payload)
                    # Phase 1: structured error for failed turns
                    if result.turn_status == "failed" and self.config.structured_errors:
                        await self._emit_structured_turn_error(
                            tools=tools,
                            turn_payload=turn_payload,
                            room_id=room_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                        )
                    break
        except asyncio.TimeoutError:
            logger.error(
                "Codex turn timed out after %ss (thread=%s, turn=%s)",
                self.config.turn_timeout_s,
                thread_id,
                turn_id,
            )
            if turn_id:
                try:
                    await self._client.request(
                        "turn/interrupt",
                        {"threadId": thread_id, "turnId": turn_id},
                    )
                except Exception:
                    logger.warning(
                        "Failed to send turn/interrupt after timeout",
                        exc_info=True,
                    )
            result.turn_status = "interrupted"
            result.turn_error = "Turn timed out"
        return result

    async def on_cleanup(self, room_id: str) -> None:
        # NOTE: _rpc_lock is adapter-wide, so cleanup for room B blocks if
        # room A holds the lock during a pending manual approval (up to
        # approval_wait_timeout_s).  This is a known limitation of the single-
        # client architecture — the lock serializes all turn processing and
        # cleanup across rooms.
        async with self._rpc_lock:
            thread_id = self._room_threads.pop(room_id, None)
            if thread_id:
                self._token_usage.pop(thread_id, None)
            self._prompt_injected_rooms.discard(room_id)
            self._raw_history_by_room.pop(room_id, None)
            self._needs_history_injection.discard(room_id)
            self._clear_pending_approvals_for_room(room_id)
            self._approval_audit.pop(room_id, None)
            self._session_approved.pop(room_id, None)
            self._sandbox_overrides.pop(room_id, None)
            if self._room_threads:
                return
            if self._client is None:
                return
            try:
                close_coro = self._client.close()
                timeout = self.config.client_close_timeout_s
                if timeout is not None:
                    try:
                        await asyncio.wait_for(close_coro, timeout=timeout)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Codex client.close() exceeded %ss timeout; "
                            "dropping client reference",
                            timeout,
                        )
                else:
                    await close_coro
            finally:
                self._client = None
                self._initialized = False
                self._selected_model = None
                self._task_titles_by_id.clear()
                # Defensive: wipe all pending approvals globally on last-room
                # teardown.  Per-room cleanup already resolves futures to
                # "decline" via _clear_pending_approvals_for_room, so this
                # catches any leaked entries from rooms whose cleanup failed.
                self._pending_approvals.clear()
                self._token_usage.clear()
                self._approval_audit.clear()
                self._session_approved.clear()
                self._sandbox_overrides.clear()

    async def _ensure_client_ready(self) -> None:
        if self._client is None:
            self._client = self._build_client(self.config)

        if not self._initialized:
            await self._client.connect()
            await self._client.initialize(
                client_name=self.config.client_name,
                client_title=self.config.client_title,
                client_version=self.config.client_version,
                experimental_api=self.config.experimental_api,
            )
            self._selected_model = await self._select_model()
            self._initialized = True

    def _build_client(self, config: CodexAdapterConfig) -> _CodexClientProtocol:
        if self._client_factory is not None:
            return self._client_factory(config)

        if config.transport == "ws":
            return CodexWebSocketClient(ws_url=config.codex_ws_url)

        if config.transport == "sdk":
            return self._build_sdk_client(config)

        return CodexStdioClient(
            command=config.codex_command,
            cwd=config.cwd,
            env=config.codex_env,
        )

    def _build_sdk_client(self, config: CodexAdapterConfig) -> _CodexClientProtocol:
        """Build a client backed by the official ``codex-app-server`` SDK."""
        from thenvoi.integrations.codex.sdk_client import CodexSdkClient

        codex_bin: str | None = None
        if config.codex_command:
            codex_bin = config.codex_command[0]

        env_dict: dict[str, str] | None = (
            dict(config.codex_env) if config.codex_env else None
        )

        # Give the SDK bridge enough headroom to outlive the adapter's
        # manual-approval wait.  A 30s margin covers event-loop latency and
        # the couple of notifications that follow the approval resolution.
        bridge_timeout_s = config.approval_wait_timeout_s + 30.0

        sdk_client = CodexSdkClient(
            cwd=config.cwd,
            env=env_dict,
            codex_bin=codex_bin,
            client_name=config.client_name,
            client_title=config.client_title,
            client_version=config.client_version,
            experimental_api=config.experimental_api,
            server_request_timeout_s=bridge_timeout_s,
        )
        sdk_client.set_request_handler(self._handle_sdk_server_request)
        return sdk_client

    async def _handle_sdk_server_request(self, event: RpcEvent) -> None:
        """Adapter callback used by :class:`CodexSdkClient` to process
        server-initiated requests (tool calls, approvals).

        This runs on the main event loop (scheduled via
        ``asyncio.run_coroutine_threadsafe``).  It must call
        ``self._client.respond()`` to unblock the SDK thread.
        """
        if self._sdk_request_context is None:
            logger.warning(
                "SDK server request received but no request context set (method=%s)",
                event.method,
            )
            if self._client is not None and event.id is not None:
                # Default to ``decline`` for approval methods so a missing
                # context can never silent-accept a command or file change.
                default = (
                    {"decision": "decline"}
                    if event.method in CODEX_APPROVAL_METHODS
                    else {}
                )
                await self._client.respond(event.id, default)
            return
        tools, msg, room_id = self._sdk_request_context
        await self._handle_server_request(
            tools=tools,
            msg=msg,
            room_id=room_id,
            event=event,
        )

    async def _select_model(self) -> str:
        if self.config.model:
            return self.config.model

        if self._client is None:
            raise RuntimeError("Codex client not initialized")
        try:
            result = await self._client.request("model/list", {})
        except Exception:
            logger.warning(
                "model/list failed; using first configured fallback model",
                exc_info=True,
            )
            return self._first_configured_fallback()

        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            return self._first_configured_fallback()

        visible_models = [
            entry
            for entry in data
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and not bool(entry.get("hidden", False))
        ]
        for entry in visible_models:
            model_id = str(entry["id"])
            if "codex" in model_id:
                return model_id
        if visible_models:
            return str(visible_models[0]["id"])
        return self._first_configured_fallback()

    def _first_configured_fallback(self) -> str:
        """Return the first operator-configured fallback, or the hard default.

        ``_select_model`` calls this when ``model/list`` fails or returns no
        visible models — honouring ``config.fallback_models`` here keeps the
        operator's override authoritative across both initial auto-selection
        and post-failure retries (``_find_fallback_model``).
        """
        fallbacks = self.config.fallback_models
        if fallbacks:
            return fallbacks[0]
        return _DEFAULT_MODEL

    async def _ensure_thread(
        self,
        *,
        room_id: str,
        history: CodexSessionState,
        tools: AgentToolsProtocol,
        is_session_bootstrap: bool,
    ) -> str:
        thread_id = self._room_threads.get(room_id)
        if thread_id:
            return thread_id

        if self._client is None:
            raise RuntimeError("Codex client not initialized")

        if is_session_bootstrap and history.has_thread():
            try:
                result = await self._client.request(
                    "thread/resume",
                    {
                        "threadId": history.thread_id,
                        "personality": self.config.personality,
                    },
                )
                resumed = result.get("thread", {}) if isinstance(result, dict) else {}
                thread_id = str(resumed.get("id") or history.thread_id or "")
                if thread_id:
                    self._room_threads[room_id] = thread_id
                    self._raw_history_by_room.pop(room_id, None)
                    if Emit.TASK_EVENTS in self.features.emit:
                        await tools.send_event(
                            content=self._build_task_event_content(
                                task_id=thread_id,
                                task="Codex thread",
                                status="resumed",
                                summary=f"Room: {room_id}",
                            ),
                            message_type="task",
                            metadata={
                                "codex_thread_id": thread_id,
                                "codex_room_id": room_id,
                                "codex_resumed": True,
                            },
                        )
                    return thread_id
            except CodexJsonRpcError as exc:
                logger.warning(
                    "thread/resume failed for room %s thread %s: %s",
                    room_id,
                    history.thread_id,
                    exc,
                )
                if self.config.inject_history_on_resume_failure:
                    self._needs_history_injection.add(room_id)
        else:
            # Not a bootstrap resume — clean up any stashed history
            self._raw_history_by_room.pop(room_id, None)

        dynamic_tools = self._build_dynamic_tools(tools)
        start_params: dict[str, Any] = {
            "model": self._selected_model,
            "cwd": self.config.cwd,
            "approvalPolicy": self.config.approval_policy,
            "personality": self.config.personality,
            "dynamicTools": dynamic_tools,
        }
        self._apply_thread_sandbox(start_params, room_id=room_id)

        started = await self._client.request("thread/start", start_params)
        thread = started.get("thread") if isinstance(started, dict) else {}
        thread_id = str((thread or {}).get("id") or "")
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")

        self._room_threads[room_id] = thread_id

        if Emit.TASK_EVENTS in self.features.emit:
            await tools.send_event(
                content=self._build_task_event_content(
                    task_id=thread_id,
                    task="Codex thread",
                    status="mapped",
                    summary=f"Transport: {self.config.transport}",
                ),
                message_type="task",
                metadata={
                    "codex_thread_id": thread_id,
                    "codex_room_id": room_id,
                    "codex_created_at": datetime.now(timezone.utc).isoformat(),
                    "codex_transport": self.config.transport,
                },
            )

        return thread_id

    def _build_dynamic_tools(self, tools: AgentToolsProtocol) -> list[dict[str, Any]]:
        dynamic_tools: list[dict[str, Any]] = []
        seen: set[str] = set()

        for schema in tools.get_openai_tool_schemas(
            include_memory=Capability.MEMORY in self.features.capabilities,
            include_contacts=Capability.CONTACTS in self.features.capabilities,
        ):
            if not isinstance(schema, dict):
                continue

            if schema.get("type") == "function":
                function = schema.get("function") if isinstance(schema, dict) else {}
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                if not isinstance(name, str) or not name or name in seen:
                    continue
                dynamic_tools.append(
                    {
                        "name": name,
                        "description": str(function.get("description") or ""),
                        "inputSchema": function.get("parameters")
                        if isinstance(function.get("parameters"), dict)
                        else {"type": "object", "properties": {}},
                    }
                )
                seen.add(name)
                continue

            name = schema.get("name")
            input_schema = schema.get("inputSchema") or schema.get("input_schema")
            if not isinstance(name, str) or not name or name in seen:
                continue
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            dynamic_tools.append(
                {
                    "name": name,
                    "description": str(schema.get("description") or ""),
                    "inputSchema": input_schema,
                }
            )
            seen.add(name)

        for tool in self.config.additional_dynamic_tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name or name in seen:
                continue
            dynamic_tools.append(tool)
            seen.add(name)

        for model_cls, _ in self._custom_tools:
            schema = custom_tool_to_openai_schema(model_cls)
            function = schema.get("function", {})
            name = function.get("name", "")
            if not name or name in seen:
                continue
            dynamic_tools.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "inputSchema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
            seen.add(name)

        return dynamic_tools

    def _build_turn_input(
        self,
        *,
        msg: PlatformMessage,
        participants_msg: str | None,
        contacts_msg: str | None,
        room_id: str,
    ) -> tuple[list[dict[str, str]], bool]:
        items: list[dict[str, str]] = []
        injected_system_prompt = False

        if room_id not in self._prompt_injected_rooms and self._system_prompt:
            items.append(
                {
                    "type": "text",
                    "text": "[System Instructions]\n" + self._system_prompt,
                }
            )
            injected_system_prompt = True

        if room_id in self._needs_history_injection:
            self._needs_history_injection.discard(room_id)
            raw_history = self._raw_history_by_room.pop(room_id, None)
            if raw_history:
                context = self._format_history_context(raw_history)
                if context:
                    items.append({"type": "text", "text": context})

        if participants_msg:
            items.append({"type": "text", "text": f"[System]: {participants_msg}"})

        if contacts_msg:
            items.append({"type": "text", "text": f"[System]: {contacts_msg}"})

        items.append({"type": "text", "text": msg.format_for_llm()})
        return items, injected_system_prompt

    def _format_history_context(self, raw: list[dict[str, Any]]) -> str | None:
        text_messages: list[str] = []
        for entry in raw:
            msg_type = entry.get("message_type", "")
            if msg_type not in {"text", "message"}:
                continue
            content = entry.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            sender = entry.get("sender_name") or entry.get("sender_type") or "Unknown"
            text_messages.append(f"[{sender}]: {content}")

        if not text_messages:
            return None

        truncated = text_messages[-self.config.max_history_messages :]
        header = (
            "[Conversation History]\n"
            "The following is the conversation history from a previous session. "
            "Use it to maintain continuity.\n"
        )
        return header + "\n".join(truncated)

    async def _handle_server_request(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
    ) -> bool:
        """Dispatch a server-initiated request (tool call, approval).

        Concurrency model: this coroutine mutates adapter state
        (``_pending_approvals``, ``_session_approved``, ``_approval_audit``,
        ``_task_titles_by_id``) without an explicit lock.  It is safe
        because the only two call sites both run on the single adapter
        event loop:

        1. The turn-processing loop in ``_process_turn_events`` (stdio / ws
           transports) which is already serialized by ``_rpc_lock``.
        2. The SDK bridge's ``_handle_sdk_server_request``, which is
           scheduled via ``asyncio.run_coroutine_threadsafe`` on the same
           loop while a turn is awaiting ``recv_event``.

        Because asyncio runs one coroutine at a time, every synchronous
        span inside this method is atomic.  If a new caller is ever added
        from a different thread, this routine must be re-audited.
        """
        if self._client is None:
            raise RuntimeError("CodexAdapter client is None — was on_started() called?")
        if event.id is None:
            return False

        params = event.params if isinstance(event.params, dict) else {}

        if event.method == "item/tool/call":
            tool_name = str(params.get("tool") or "")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = str(params.get("callId") or "")
            tool_call_succeeded = False

            # Don't emit reporting for platform tools that already produce
            # visible output (messages/events) — reporting them is redundant.
            should_report = (
                Emit.EXECUTION in self.features.emit
                and tool_name not in _SILENT_REPORTING_TOOLS
            )

            if should_report:
                await tools.send_event(
                    content=json.dumps(
                        {"name": tool_name, "args": arguments, "tool_call_id": call_id}
                    ),
                    message_type="tool_call",
                )

            try:
                custom_tool = find_custom_tool(self._custom_tools, tool_name)
                if custom_tool:
                    result = await execute_custom_tool(custom_tool, arguments)
                else:
                    result = await tools.execute_tool_call(tool_name, arguments)
                text_result = (
                    result
                    if isinstance(result, str)
                    else json.dumps(result, default=str)
                )
                success = True
                await self._client.respond(
                    event.id,
                    {
                        "contentItems": [{"type": "inputText", "text": text_result}],
                        "success": success,
                    },
                )
                tool_call_succeeded = True
                if should_report:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": tool_name,
                                "output": text_result,
                                "tool_call_id": call_id,
                            }
                        ),
                        message_type="tool_result",
                    )
            except ValidationError as exc:
                errors = "; ".join(
                    f"{err['loc'][0]}: {err['msg']}" for err in exc.errors()
                )
                error_text = f"Invalid arguments for {tool_name}: {errors}"
                logger.error("Validation error for tool %s: %s", tool_name, exc)
                await self._client.respond(
                    event.id,
                    {
                        "contentItems": [{"type": "inputText", "text": error_text}],
                        "success": False,
                    },
                )
                if should_report:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": tool_name,
                                "output": error_text,
                                "tool_call_id": call_id,
                            }
                        ),
                        message_type="tool_result",
                    )
            except Exception as exc:
                error_text = f"Error: {exc}"
                logger.exception("Tool execution failed for %s", tool_name)
                await self._client.respond(
                    event.id,
                    {
                        "contentItems": [{"type": "inputText", "text": error_text}],
                        "success": False,
                    },
                )
                if should_report:
                    await tools.send_event(
                        content=json.dumps(
                            {
                                "name": tool_name,
                                "output": error_text,
                                "tool_call_id": call_id,
                            }
                        ),
                        message_type="tool_result",
                    )

            return tool_name == "thenvoi_send_message" and tool_call_succeeded

        if event.method in CODEX_APPROVAL_METHODS:
            await self._handle_approval_request(
                tools=tools,
                msg=msg,
                room_id=room_id,
                event=event,
                params=params,
            )
            return False

        await self._client.respond_error(
            event.id,
            code=-32601,
            message=f"Unhandled server request: {event.method}",
        )
        return False

    async def _handle_approval_request(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
        params: dict[str, Any],
    ) -> None:
        """Resolve a Codex approval request (command / file change).

        Splits off from :meth:`_handle_server_request` for readability.
        Same concurrency guarantees apply — see the docstring there.
        """
        if self._client is None:
            raise RuntimeError("CodexAdapter client is None — was on_started() called?")
        if event.id is None:
            return

        summary = self._approval_summary(event.method, params)

        # Check session-level auto-approval first.
        session_key = self._session_approval_key(event.method, params)
        room_session = self._session_approved.get(room_id)
        session_hit = bool(session_key and room_session and session_key in room_session)

        if session_hit:
            decision: ApprovalDecision = "acceptForSession"
            decided_by = "session_policy"
        elif self.config.approval_mode == "manual":
            try:
                decision = await self._resolve_manual_approval(
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    event=event,
                    summary=summary,
                    params=params,
                )
                decided_by = msg.sender_name or msg.sender_type or "user"
            except Exception:
                # Ensure we still answer the server request even if the
                # human-facing notification flow fails.
                logger.exception("Manual approval flow failed; defaulting to decline")
                decision = "decline"
                decided_by = "system_fallback"
        else:
            decision = (
                "accept" if self.config.approval_mode == "auto_accept" else "decline"
            )
            decided_by = f"policy:{self.config.approval_mode}"

        await self._client.respond(event.id, {"decision": decision})

        audit_entry = self._record_approval_audit(
            room_id=room_id,
            request_id=str(event.id),
            method=event.method,
            decision=decision,
            decided_by=decided_by,
            summary=summary,
            session_level=session_hit,
        )
        await self._emit_approval_audit_event(
            tools=tools,
            room_id=room_id,
            entry=audit_entry,
        )

        if (
            self.config.approval_mode != "manual"
            and self.config.approval_text_notifications
        ):
            mention = [
                {
                    "id": msg.sender_id,
                    "name": msg.sender_name or msg.sender_type,
                }
            ]
            try:
                await tools.send_message(
                    f"Approval requested ({summary}). Policy decision: {decision}.",
                    mentions=mention,
                )
            except Exception:
                logger.exception("Failed to send approval policy notification")

        if Emit.THOUGHTS in self.features.emit:
            try:
                await tools.send_event(
                    content=(
                        f"Codex approval request handled automatically ({decision})."
                    ),
                    message_type="thought",
                    metadata={
                        "codex_approval_method": event.method,
                        "codex_approval_type": self._approval_type(event.method),
                        "codex_approval_options": [
                            "accept",
                            "acceptForSession",
                            "decline",
                        ],
                    },
                )
            except Exception:
                # Best-effort telemetry — never fail the turn on thought
                # emission failures.
                logger.debug(
                    "Failed to emit approval thought event",
                    exc_info=True,
                )

    async def _emit_turn_outcome(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        turn_status: str,
        turn_error: str,
        final_text: str,
        saw_send_message_tool: bool,
        duration_s: float = 0.0,
    ) -> None:
        # Look up token usage once for both marker and lifecycle events.
        usage = self._token_usage.get(thread_id)
        has_usage = usage is not None and usage.total_tokens > 0

        if (
            Emit.TASK_EVENTS in self.features.emit
            and self.config.emit_turn_task_markers
        ):
            summary = f"Thread: {thread_id}"
            if turn_error:
                summary += f" | Error: {turn_error}"
            metadata: dict[str, Any] = {
                "codex_room_id": room_id,
                "codex_thread_id": thread_id,
                "codex_turn_id": turn_id,
                "codex_turn_status": turn_status,
                "codex_error": turn_error or None,
            }
            if duration_s > 0:
                metadata["codex_duration_s"] = round(duration_s, 2)
            if has_usage:
                metadata.update(usage.to_metadata())
            await tools.send_event(
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex turn",
                    status=turn_status,
                    summary=summary,
                ),
                message_type="task",
                metadata=metadata,
            )

        # Phase 2: Enriched turn lifecycle events
        if (
            self.config.emit_turn_lifecycle_events
            and Emit.TASK_EVENTS in self.features.emit
        ):
            lifecycle_metadata: dict[str, Any] = {
                "codex_event_type": "turn_lifecycle",
                "codex_room_id": room_id,
                "codex_thread_id": thread_id,
                "codex_turn_id": turn_id,
                "codex_turn_status": turn_status,
            }
            # Match the marker path: only include duration once it's been
            # measured.  Avoids a misleading "0.0s" on synthetic paths that
            # emit lifecycle events without timing (e.g. future code paths
            # that reuse _emit_turn_outcome without a real turn_start).
            if duration_s > 0:
                lifecycle_metadata["codex_duration_s"] = round(duration_s, 2)
            if turn_error:
                lifecycle_metadata["codex_error"] = turn_error
            if has_usage:
                lifecycle_metadata.update(usage.to_metadata())
            try:
                await tools.send_event(
                    content=self._build_task_event_content(
                        task_id=turn_id,
                        task="Codex turn lifecycle",
                        status=turn_status,
                        summary=f"Duration: {duration_s:.1f}s | Thread: {thread_id}",
                    ),
                    message_type="task",
                    metadata=lifecycle_metadata,
                )
            except Exception:
                logger.debug("Failed to emit turn lifecycle event", exc_info=True)

        mention = [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]

        if turn_status == "completed":
            if (
                self.config.fallback_send_agent_text
                and final_text.strip()
                and not saw_send_message_tool
            ):
                await tools.send_message(final_text.strip(), mentions=mention)
            return

        if turn_status == "interrupted":
            await tools.send_message(
                "I stopped before completing this request.",
                mentions=mention,
            )
            return

        error_text = (
            f"I couldn't complete this request ({turn_status})."
            if not turn_error
            else f"I couldn't complete this request ({turn_status}): {turn_error}"
        )
        await tools.send_message(error_text, mentions=mention)

    async def _emit_item_completed_events(
        self,
        *,
        tools: AgentToolsProtocol,
        item: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Forward internal Codex operations as platform events.

        Best-effort: failures are logged but never propagated, since
        execution reporting is supplementary and must not kill message
        processing (e.g. a transient 403 from the load balancer).
        """
        item_type = item.get("type", "")
        item_id = str(item.get("id") or "")
        metadata = {
            "codex_room_id": room_id,
            "codex_thread_id": thread_id,
            "codex_turn_id": turn_id,
        }

        try:
            await self._emit_item_event(
                tools=tools,
                item_type=item_type,
                item_id=item_id,
                item=item,
                metadata=metadata,
            )
        except Exception:
            # Best-effort telemetry — never fail the turn on emit failures.
            logger.debug(
                "Failed to emit %s event for item %s (best-effort)",
                item_type,
                item_id,
                exc_info=True,
            )

    async def _emit_item_event(
        self,
        *,
        tools: AgentToolsProtocol,
        item_type: str,
        item_id: str,
        item: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Inner dispatch for item events — may raise on API errors."""
        # Tool-like items gated on Emit.EXECUTION
        if item_type in {
            "commandExecution",
            "fileChange",
            "mcpToolCall",
            "webSearch",
            "imageView",
            "collabAgentToolCall",
        }:
            if Emit.EXECUTION not in self.features.emit:
                return
            name, args, output = self._extract_tool_item(item_type, item)
            await tools.send_event(
                content=json.dumps(
                    {"name": name, "args": args, "tool_call_id": item_id}
                ),
                message_type="tool_call",
                metadata=metadata,
            )
            await tools.send_event(
                content=json.dumps(
                    {"name": name, "output": output, "tool_call_id": item_id}
                ),
                message_type="tool_result",
                metadata=metadata,
            )
            return

        # Thought-like items gated on Emit.THOUGHTS
        if item_type in {
            "reasoning",
            "plan",
            "contextCompaction",
            "enteredReviewMode",
            "exitedReviewMode",
        }:
            if Emit.THOUGHTS not in self.features.emit:
                return
            text = self._extract_thought_text(item_type, item)
            await tools.send_event(
                content=text,
                message_type="thought",
                metadata=metadata,
            )
            return

        # Skip known non-actionable types
        if item_type in {"userMessage", "agentMessage"}:
            return

        logger.debug("Unhandled item/completed type: %s", item_type)

    @staticmethod
    def _extract_tool_item(
        item_type: str, item: dict[str, Any]
    ) -> tuple[str, dict[str, Any], str]:
        """Extract (name, args, output) for a tool-like item."""
        if item_type == "commandExecution":
            command = item.get("command", "")
            cwd = item.get("cwd", "")
            args: dict[str, Any] = {"command": command, "cwd": cwd}
            output_parts: list[str] = []
            if item.get("aggregated_output"):
                output_parts.append(str(item["aggregated_output"]))
            exit_code = item.get("exitCode")
            if exit_code is not None:
                output_parts.append(f"exit_code={exit_code}")
            status = item.get("status", "")
            output = "\n".join(output_parts) if output_parts else str(status)
            return "exec", args, output

        if item_type == "fileChange":
            changes = item.get("changes", [])
            if not isinstance(changes, list):
                changes = []
            file_paths = [c.get("path", "") for c in changes if isinstance(c, dict)]
            return (
                "file_edit",
                {"files": file_paths},
                str(item.get("status", "applied")),
            )

        if item_type == "mcpToolCall":
            server = item.get("server", "")
            tool = item.get("tool", "")
            name = f"mcp:{server}/{tool}"
            mcp_args = item.get("arguments", {})
            if not isinstance(mcp_args, dict):
                mcp_args = {}
            result = item.get("result")
            error = item.get("error")
            if result is not None:
                output = json.dumps(result, default=str)
            elif error is not None:
                output = json.dumps(error, default=str)
            else:
                output = "completed"
            return name, mcp_args, output

        if item_type == "webSearch":
            query = item.get("query", "")
            action = item.get("action")
            output = json.dumps(action, default=str) if action else "completed"
            return "web_search", {"query": query}, output

        if item_type == "imageView":
            path = item.get("path", "")
            return "view_image", {"path": path}, str(item.get("status", "viewed"))

        if item_type == "collabAgentToolCall":
            collab_tool = item.get("tool", "")
            name = f"collab:{collab_tool}"
            collab_args: dict[str, Any] = {}
            if item.get("prompt"):
                collab_args["prompt"] = item["prompt"]
            if item.get("agents"):
                collab_args["agents"] = item["agents"]
            result = item.get("result")
            output = (
                json.dumps(result, default=str) if result is not None else "completed"
            )
            return name, collab_args, output

        return item_type, {}, "completed"

    @staticmethod
    def _extract_thought_text(item_type: str, item: dict[str, Any]) -> str:
        """Extract display text for a thought-like item."""
        if item_type == "reasoning":
            summary = item.get("summary", [])
            if isinstance(summary, list):
                return "\n".join(str(s) for s in summary) or "(reasoning)"
            return str(summary) or "(reasoning)"

        if item_type == "plan":
            return str(item.get("text", "")) or "(plan)"

        if item_type == "contextCompaction":
            return "Context compaction performed"

        if item_type in {"enteredReviewMode", "exitedReviewMode"}:
            text = item.get("text", "")
            if text:
                return str(text)
            return f"Review mode: {item_type}"

        return str(item.get("text", "")) or item_type

    async def _resolve_manual_approval(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        event: RpcEvent,
        summary: str,
        params: dict[str, Any],
    ) -> ApprovalDecision:
        mention = [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]
        if event.id is None:
            raise RuntimeError("approval request must have an id")
        token = self._approval_token(event.id, params)
        loop = asyncio.get_running_loop()
        pending = _PendingApproval(
            request_id=event.id,
            method=event.method,
            summary=summary,
            created_at=datetime.now(timezone.utc),
            future=loop.create_future(),
            session_key=self._session_approval_key(event.method, params),
        )
        room_pending = self._pending_approvals.setdefault(room_id, {})
        if len(room_pending) >= self.config.max_pending_approvals_per_room:
            oldest_token = min(room_pending, key=lambda t: room_pending[t].created_at)
            evicted = room_pending.pop(oldest_token)
            if not evicted.future.done():
                evicted.future.set_result("decline")
            logger.warning(
                "Evicted oldest pending approval %s in room %s (limit %s)",
                oldest_token,
                room_id,
                self.config.max_pending_approvals_per_room,
            )
        room_pending[token] = pending
        try:
            approval_msg = (
                "Approval requested "
                f"({summary}). Approval id: `{token}`. "
                f"Reply `/approve {token}` or `/decline {token}` "
                f"or `/approve-session {token}` (approve all similar for this session). "
                "Use `/approvals` to list pending approvals."
            )
            # Emit enriched metadata as a task event for UI rendering
            if Emit.TASK_EVENTS in self.features.emit:
                approval_metadata: dict[str, Any] = {
                    "codex_event_type": "approval_request",
                    "codex_approval_type": self._approval_type(event.method),
                    "codex_approval_method": event.method,
                    "codex_room_id": room_id,
                    "codex_approval_options": [
                        "accept",
                        "acceptForSession",
                        "decline",
                    ],
                }
                if params.get("command"):
                    approval_metadata["codex_command"] = params["command"]
                if params.get("cwd"):
                    approval_metadata["codex_cwd"] = params["cwd"]
                if params.get("reason"):
                    approval_metadata["codex_reason"] = params["reason"]
                # Network context (domains, IPs) when available
                net_ctx = params.get("networkContext") or params.get("network_context")
                if net_ctx:
                    approval_metadata["codex_network_context"] = net_ctx
                try:
                    await tools.send_event(
                        content=self._build_task_event_content(
                            task_id=token,
                            task="Codex approval request",
                            status="pending",
                            summary=summary,
                        ),
                        message_type="task",
                        metadata=approval_metadata,
                    )
                except Exception:
                    logger.debug(
                        "Failed to emit approval request task event",
                        exc_info=True,
                    )
            await tools.send_message(approval_msg, mentions=mention)
            decision_raw = await asyncio.wait_for(
                pending.future,
                timeout=self.config.approval_wait_timeout_s,
            )
            if decision_raw in {"accept", "acceptForSession"}:
                return decision_raw  # type: ignore[return-value]
            return "decline"
        except asyncio.TimeoutError:
            timeout_decision = self.config.approval_timeout_decision
            try:
                await tools.send_message(
                    f"Approval `{token}` timed out. Applied `{timeout_decision}`.",
                    mentions=mention,
                )
            except Exception:
                logger.exception("Failed to send approval timeout notification")
            return timeout_decision
        finally:
            self._clear_pending_approval(room_id, token)

    async def _forward_raw_task_event(
        self,
        *,
        tools: AgentToolsProtocol,
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if Emit.TASK_EVENTS not in self.features.emit:
            return

        is_started = method == "codex/event/task_started"
        task_phase = "started" if is_started else "completed"
        task_id = self._task_event_id(params)
        title = self._task_event_title(params)
        if task_id and title and is_started:
            self._task_titles_by_id[task_id] = title
            if len(self._task_titles_by_id) > self._max_task_titles:
                self._task_titles_by_id.popitem(last=False)
        if task_id and not title:
            title = self._task_titles_by_id.get(task_id)
        summary = self._task_event_summary(params)
        if not title:
            title = "Codex task lifecycle event"
            if not summary:
                summary = f"Method: {method}"
        content = self._build_task_event_content(
            task_id=task_id,
            task=title,
            status=task_phase,
            summary=summary,
        )

        metadata: dict[str, Any] = {
            "codex_room_id": room_id,
            "codex_thread_id": thread_id,
            "codex_turn_id": turn_id,
            "codex_event_method": method,
            "codex_task_phase": task_phase,
        }
        if task_id:
            metadata["codex_task_id"] = task_id
        if params:
            metadata["codex_event_params"] = params

        await tools.send_event(
            content=content,
            message_type="task",
            metadata=metadata,
        )
        if not is_started and task_id:
            self._task_titles_by_id.pop(task_id, None)

    # ------------------------------------------------------------------
    # Phase 1: Structured error handling
    # ------------------------------------------------------------------

    async def _handle_error_event(
        self,
        *,
        tools: AgentToolsProtocol,
        params: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Handle an ``error`` notification from Codex."""
        error_obj = params.get("error") or {}
        if isinstance(error_obj, dict):
            error_msg = error_obj.get("message", "")
        else:
            error_msg = str(error_obj)
            error_obj = {"message": error_msg}
        will_retry = bool(params.get("willRetry", False))
        if will_retry:
            # Include retry metadata so a retry storm is observable in logs
            # even though we don't surface transient errors to the user.
            retry_attempt = params.get("retryAttempt") or params.get("attempt")
            retry_after_s = params.get("retryAfterSeconds") or params.get("retryAfter")
            logger.warning(
                "Codex transient error (will retry, attempt=%s, retry_after=%ss, "
                "thread=%s, turn=%s): %s",
                retry_attempt,
                retry_after_s,
                thread_id,
                turn_id,
                error_msg,
            )
            return

        logger.error("Codex error: %s", error_msg)
        if self.config.structured_errors:
            content, err_meta = build_structured_error_metadata(
                error_obj, thread_id=thread_id, turn_id=turn_id
            )
            err_meta["codex_room_id"] = room_id
            await tools.send_event(
                content=content or f"Codex error: {error_msg}",
                message_type="error",
                metadata=err_meta,
            )
        else:
            await tools.send_event(
                content=f"Codex error: {error_msg}",
                message_type="error",
                metadata={
                    "codex_room_id": room_id,
                    "codex_thread_id": thread_id,
                    "codex_turn_id": turn_id,
                },
            )

    async def _emit_structured_turn_error(
        self,
        *,
        tools: AgentToolsProtocol,
        turn_payload: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Emit a structured error event when turn/completed reports failure."""
        error = turn_payload.get("error")
        if not isinstance(error, dict):
            return
        content, err_meta = build_structured_error_metadata(
            error, thread_id=thread_id, turn_id=turn_id
        )
        err_meta["codex_room_id"] = room_id
        try:
            await tools.send_event(
                content=content,
                message_type="error",
                metadata=err_meta,
            )
        except Exception:
            logger.debug("Failed to emit structured turn error", exc_info=True)

    # ------------------------------------------------------------------
    # Phase 2: Plan step tracking
    # ------------------------------------------------------------------

    async def _forward_plan_steps(
        self,
        *,
        tools: AgentToolsProtocol,
        params: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Forward turn/plan/updated with structured step-level status."""
        steps = parse_plan_steps(params)
        if not steps:
            return
        step_dicts = [{"step": s.step, "status": s.status} for s in steps]
        try:
            await tools.send_event(
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex plan",
                    status="updated",
                    summary=f"{len(steps)} steps",
                ),
                message_type="task",
                metadata={
                    "codex_plan_steps": step_dicts,
                    "codex_room_id": room_id,
                    "codex_thread_id": thread_id,
                    "codex_turn_id": turn_id,
                },
            )
        except Exception:
            logger.debug("Failed to forward plan steps", exc_info=True)

    # ------------------------------------------------------------------
    # Phase 4: Token usage & diffs
    # ------------------------------------------------------------------

    def _update_token_usage(self, thread_id: str, params: dict[str, Any]) -> None:
        """Update accumulated token usage for a thread."""
        usage = self._token_usage.get(thread_id)
        if usage is None:
            usage = CodexTokenUsage()
            self._token_usage[thread_id] = usage
        usage.update(params)

    async def _emit_token_usage_event(
        self,
        *,
        tools: AgentToolsProtocol,
        thread_id: str,
        room_id: str,
    ) -> None:
        """Emit a task event with current token usage."""
        usage = self._token_usage.get(thread_id)
        # Dataclass instances are always truthy, so check for None and the
        # zero-tokens sentinel explicitly — otherwise we'd emit an empty event
        # before any thread/tokenUsage/updated notification has arrived.
        if usage is None or usage.total_tokens == 0:
            return
        metadata = usage.to_metadata()
        metadata["codex_thread_id"] = thread_id
        metadata["codex_room_id"] = room_id
        try:
            await tools.send_event(
                content=usage.format_summary(),
                message_type="task",
                metadata=metadata,
            )
        except Exception:
            logger.debug("Failed to emit token usage event", exc_info=True)

    async def _forward_diff_event(
        self,
        *,
        tools: AgentToolsProtocol,
        params: dict[str, Any],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        """Forward turn/diff/updated as a task event.

        Diffs are semantically separate from tool results, so we emit a task
        event with ``codex_event_type=turn_diff`` and carry the raw diff in
        metadata.  Consumers filter on ``codex_event_type`` rather than the
        generic ``tool_result`` channel.
        """
        diff_content = str(params.get("diff") or params.get("content") or "")
        files_changed: list[str] = []
        files_raw = params.get("files") or params.get("filesChanged") or []
        if isinstance(files_raw, list):
            files_changed = [str(f) for f in files_raw if f]
        summary = (
            f"{len(files_changed)} files changed" if files_changed else "diff updated"
        )

        # Enforce the cap on UTF-8 bytes (not Python characters) so a diff
        # loaded with emoji or CJK can't silently inflate the WebSocket
        # frame well past the intended budget.
        diff_truncated = False
        diff_bytes = diff_content.encode("utf-8")
        original_byte_length = len(diff_bytes)
        original_length = len(diff_content)
        if original_byte_length > _MAX_DIFF_METADATA_BYTES:
            truncated_bytes = diff_bytes[:_MAX_DIFF_METADATA_BYTES]
            # Drop any trailing partial UTF-8 sequence introduced by slicing
            # inside a multi-byte character.
            diff_content = truncated_bytes.decode("utf-8", errors="ignore") + (
                f"\n... [truncated, {original_byte_length - _MAX_DIFF_METADATA_BYTES} "
                f"more bytes]"
            )
            diff_truncated = True

        metadata: dict[str, Any] = {
            "codex_event_type": "turn_diff",
            "codex_thread_id": thread_id,
            "codex_turn_id": turn_id,
            "codex_room_id": room_id,
            "codex_files_changed": files_changed,
            "codex_diff": diff_content,
        }
        if diff_truncated:
            metadata["codex_diff_truncated"] = True
            metadata["codex_diff_original_length"] = original_length
            metadata["codex_diff_original_bytes"] = original_byte_length
        try:
            await tools.send_event(
                content=self._build_task_event_content(
                    task_id=turn_id,
                    task="Codex diff",
                    status="updated",
                    summary=summary,
                ),
                message_type="task",
                metadata=metadata,
            )
        except Exception:
            logger.debug("Failed to forward diff event", exc_info=True)

    # ------------------------------------------------------------------
    # Phase 1: Approval audit trail
    # ------------------------------------------------------------------

    def _record_session_approval(self, room_id: str, key: str) -> None:
        """Register ``key`` as a session-level auto-approval for ``room_id``.

        Maintains insertion order so the oldest entry is evicted when the
        room exceeds ``max_session_approved_per_room``.  Moving an existing
        key to the most-recent position on re-approval keeps frequently
        re-approved commands from being evicted.
        """
        room = self._session_approved.setdefault(room_id, OrderedDict())
        if key in room:
            room.move_to_end(key)
        else:
            room[key] = None
        cap = self.config.max_session_approved_per_room
        while len(room) > cap:
            room.popitem(last=False)

    def _record_approval_audit(
        self,
        *,
        room_id: str,
        request_id: str,
        method: str,
        decision: str,
        decided_by: str,
        summary: str = "",
        session_level: bool = False,
    ) -> ApprovalAuditEntry:
        """Record an approval decision and return the stored entry."""
        entry = ApprovalAuditEntry(
            request_id=str(request_id),
            method=method,
            decision=decision,
            decided_by=decided_by,
            timestamp=datetime.now(timezone.utc).isoformat(),
            summary=summary,
            session_level=session_level,
        )
        audit_list = self._approval_audit.setdefault(room_id, [])
        audit_list.append(entry)
        # Cap the audit trail to avoid unbounded memory growth in long sessions.
        if len(audit_list) > self.config.max_approval_audit_per_room:
            del audit_list[: len(audit_list) - self.config.max_approval_audit_per_room]
        return entry

    async def _emit_approval_audit_event(
        self,
        *,
        tools: AgentToolsProtocol,
        room_id: str,
        entry: ApprovalAuditEntry,
    ) -> None:
        """Emit a task event for an approval decision."""
        if Emit.TASK_EVENTS not in self.features.emit:
            return
        try:
            await tools.send_event(
                content=self._build_task_event_content(
                    task_id=str(entry.request_id),
                    task="Codex approval",
                    status=entry.decision,
                    summary=entry.summary,
                ),
                message_type="task",
                metadata={
                    "codex_event_type": "approval_resolution",
                    "codex_approval_method": entry.method,
                    "codex_approval_decision": entry.decision,
                    "codex_decided_by": entry.decided_by,
                    "codex_session_level": entry.session_level,
                    "codex_room_id": room_id,
                    "codex_timestamp": entry.timestamp,
                },
            )
        except Exception:
            logger.debug("Failed to emit approval audit event", exc_info=True)

    async def _handle_local_command(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        history: CodexSessionState,
        room_id: str,
        command: str,
        args: str,
    ) -> bool:
        mention = [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]

        if command == "help":
            await tools.send_message(
                "Codex commands: "
                "`/status`, `/model`, `/models`, `/model list`, `/models list`, `/model <id>`, "
                "`/reasoning [none|minimal|low|medium|high|xhigh]`, "
                "`/approvals`, `/approve <id>`, `/approve-session <id>`, `/decline <id>`, "
                "`/threads`, `/thread info`, `/thread archive`, "
                "`/sandbox <mode>`, `/permissions`, `/usage`, `/help`.",
                mentions=mention,
            )
            return True

        if command == "status":
            mapped_thread = self._room_threads.get(room_id) or history.thread_id or None
            usage = (
                self._token_usage.get(mapped_thread or "") if mapped_thread else None
            )
            usage_line = (
                usage.format_summary() if usage and usage.total_tokens > 0 else "none"
            )
            session_approvals = len(self._session_approved.get(room_id) or ())
            status_text = (
                "Codex status:\n"
                f"- transport: {self.config.transport}\n"
                f"- selected_model: {self._selected_model or 'unknown'}\n"
                f"- configured_model: {self.config.model or 'auto'}\n"
                f"- room_id: {room_id}\n"
                f"- thread_id: {mapped_thread or 'not mapped'}\n"
                f"- approval_policy: {self.config.approval_policy}\n"
                f"- approval_mode: {self.config.approval_mode}\n"
                f"- sandbox: {self._effective_sandbox(room_id) or 'default'}\n"
                f"- reasoning_effort: {self.config.reasoning_effort or 'default'}\n"
                f"- reasoning_summary: {self.config.reasoning_summary or 'default'}\n"
                f"- pending_approvals: {len(self._pending_approvals.get(room_id, {}))}\n"
                f"- session_approvals: {session_approvals}\n"
                f"- token_usage: {usage_line}\n"
                f"- turn_task_markers: {self.config.emit_turn_task_markers}"
            )
            await tools.send_message(status_text, mentions=mention)
            return True

        if command in {"model", "models"}:
            model_arg = args.strip()
            if not model_arg:
                await tools.send_message(
                    "Current model: "
                    f"`{self._selected_model or 'unknown'}` "
                    f"(configured: `{self.config.model or 'auto'}`). "
                    "Use `/model list` to view available models or `/model <id>` to override.",
                    mentions=mention,
                )
                return True

            if model_arg.lower() in {"list", "ls"}:
                if self._client is None:
                    raise RuntimeError("Codex client not initialized")
                result = await self._client.request("model/list", {})
                models = self._visible_model_ids(result)
                if models:
                    preview = ", ".join(models[:10])
                    if len(models) > 10:
                        preview += ", ..."
                    await tools.send_message(
                        f"Available models ({len(models)}): {preview}",
                        mentions=mention,
                    )
                else:
                    await tools.send_message(
                        "No visible models returned by Codex app-server.",
                        mentions=mention,
                    )
                return True

            self.config.model = model_arg
            self._selected_model = model_arg
            self._model_explicitly_set = True
            await tools.send_message(
                f"Model override set to `{model_arg}` for subsequent turns.",
                mentions=mention,
            )
            return True

        if command == "reasoning":
            effort_arg = args.strip().lower()
            if not effort_arg:
                await tools.send_message(
                    f"Current reasoning effort: `{self.config.reasoning_effort or 'default'}`. "
                    f"Summary: `{self.config.reasoning_summary or 'default'}`. "
                    f"Use `/reasoning <{'|'.join(sorted(_REASONING_EFFORTS))}>` to override.",
                    mentions=mention,
                )
                return True
            if effort_arg not in _REASONING_EFFORTS:
                await tools.send_message(
                    f"Invalid reasoning effort `{effort_arg}`. "
                    f"Valid values: {', '.join(sorted(_REASONING_EFFORTS))}.",
                    mentions=mention,
                )
                return True
            self.config.reasoning_effort = effort_arg  # type: ignore[assignment]  # Literal narrowed by Pydantic validation
            await tools.send_message(
                f"Reasoning effort set to `{effort_arg}` for subsequent turns.",
                mentions=mention,
            )
            return True

        # --- Phase 1: /sandbox and /permissions commands ---
        if command == "sandbox":
            if self.config.sandbox_policy is not None:
                await tools.send_message(
                    "Cannot override sandbox: a `sandbox_policy` is configured. "
                    "Remove `sandbox_policy` from config to use per-room `/sandbox` overrides.",
                    mentions=mention,
                )
                return True
            mode_arg = args.strip()
            if not mode_arg:
                effective = self._effective_sandbox(room_id) or "default"
                await tools.send_message(
                    f"Current sandbox: `{effective}`. "
                    "Use `/sandbox <read-only|workspace-write|danger-full-access>` to change.",
                    mentions=mention,
                )
                return True
            # Parse tokens explicitly so "--confirm-anything-else" is surfaced
            # as an unknown mode instead of silently stripped away.
            tokens = mode_arg.split()
            confirm_flag = "--confirm" in tokens
            mode_tokens = [tok for tok in tokens if tok != "--confirm"]
            if len(mode_tokens) != 1:
                await tools.send_message(
                    "Usage: `/sandbox <read-only|workspace-write|danger-full-access> "
                    "[--confirm]`.",
                    mentions=mention,
                )
                return True
            mode_token = mode_tokens[0]
            normalized = self._normalize_sandbox_mode(mode_token)
            if normalized is None:
                await tools.send_message(
                    f"Invalid sandbox mode `{mode_token}`. "
                    "Valid: read-only, workspace-write, danger-full-access.",
                    mentions=mention,
                )
                return True
            if normalized == "danger-full-access" and not confirm_flag:
                await tools.send_message(
                    "Escalating to `danger-full-access` removes all sandbox "
                    "restrictions. Re-run with `--confirm` to proceed:\n"
                    "`/sandbox danger-full-access --confirm`",
                    mentions=mention,
                )
                return True
            if normalized == "danger-full-access":
                logger.warning(
                    "Sandbox escalated to danger-full-access via /sandbox command "
                    "in room %s by %s",
                    room_id,
                    msg.sender_name or msg.sender_type or "unknown",
                )
            self._sandbox_overrides[room_id] = normalized
            await tools.send_message(
                f"Sandbox mode set to `{normalized}` for subsequent turns in this room.",
                mentions=mention,
            )
            return True

        if command == "permissions":
            session_approved = self._session_approved.get(room_id) or ()
            audit = self._approval_audit.get(room_id, [])
            lines = ["Effective permissions:"]
            lines.append(f"- approval_mode: {self.config.approval_mode}")
            lines.append(f"- sandbox: {self._effective_sandbox(room_id) or 'default'}")
            lines.append(f"- approval_policy: {self.config.approval_policy}")
            if session_approved:
                lines.append(f"- session_approved patterns: {len(session_approved)}")
                for pattern in sorted(session_approved):
                    lines.append(f"  - {pattern}")
            if audit:
                lines.append(f"- approval_history: {len(audit)} decisions")
                for entry in audit[-5:]:
                    lines.append(
                        f"  - [{entry.timestamp}] {entry.method}: "
                        f"{entry.decision} by {entry.decided_by}"
                    )
            await tools.send_message("\n".join(lines), mentions=mention)
            return True

        # --- Phase 2: /threads, /thread info, /thread archive ---
        if command in {"threads", "thread"}:
            subcommand = args.strip().lower()
            if command == "threads" or not subcommand:
                # List all room->thread mappings
                if not self._room_threads:
                    await tools.send_message(
                        "No active thread mappings.", mentions=mention
                    )
                    return True
                lines = ["Active thread mappings:"]
                for rid, tid in self._room_threads.items():
                    current = " (current)" if rid == room_id else ""
                    lines.append(f"- room `{rid}` → thread `{tid}`{current}")
                await tools.send_message("\n".join(lines), mentions=mention)
                return True

            if subcommand == "info":
                mapped_thread = self._room_threads.get(room_id)
                if not mapped_thread:
                    await tools.send_message(
                        "No thread mapped for this room.", mentions=mention
                    )
                    return True
                usage = self._token_usage.get(mapped_thread)
                usage_line = (
                    usage.format_summary()
                    if usage and usage.total_tokens > 0
                    else "none"
                )
                info_text = (
                    f"Thread info:\n"
                    f"- thread_id: {mapped_thread}\n"
                    f"- room_id: {room_id}\n"
                    f"- token_usage: {usage_line}"
                )
                await tools.send_message(info_text, mentions=mention)
                return True

            if subcommand == "archive":
                mapped_thread = self._room_threads.pop(room_id, None)
                self._prompt_injected_rooms.discard(room_id)
                self._token_usage.pop(mapped_thread or "", None)
                self._raw_history_by_room.pop(room_id, None)
                self._needs_history_injection.discard(room_id)
                await tools.send_message(
                    f"Thread `{mapped_thread or 'none'}` archived. "
                    "A new thread will be created on next message.",
                    mentions=mention,
                )
                return True

            return False

        # --- Phase 4: /usage command ---
        if command == "usage":
            mapped_thread = self._room_threads.get(room_id)
            if not mapped_thread:
                await tools.send_message(
                    "No thread mapped for this room — no usage data.",
                    mentions=mention,
                )
                return True
            usage = self._token_usage.get(mapped_thread)
            if not usage or usage.total_tokens == 0:
                await tools.send_message(
                    "No token usage recorded for this thread.",
                    mentions=mention,
                )
                return True
            await tools.send_message(
                f"Thread `{mapped_thread}` — {usage.format_summary()}",
                mentions=mention,
            )
            return True

        return False

    async def _handle_approval_command(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        command: str,
        args: str,
    ) -> bool:
        mention = [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]
        pending = self._pending_approvals.get(room_id, {})

        if command == "approvals":
            if not pending:
                await tools.send_message("No pending approvals.", mentions=mention)
                return True
            lines = ["Pending approvals:"]
            now = datetime.now(timezone.utc)
            for token, item in list(pending.items()):
                age_s = int((now - item.created_at).total_seconds())
                lines.append(f"- {token}: {item.summary} ({age_s}s)")
            await tools.send_message("\n".join(lines), mentions=mention)
            return True

        if command not in {"approve", "decline", "approve-session"}:
            return False

        if not pending:
            await tools.send_message(
                "No pending approvals to resolve.",
                mentions=mention,
            )
            return True

        token = args.strip().split(" ", 1)[0] if args.strip() else ""
        if token:
            selected = pending.get(token)
            if selected is None:
                available = ", ".join(sorted(pending.keys()))
                await tools.send_message(
                    f"Unknown approval id `{token}`. Pending: {available}",
                    mentions=mention,
                )
                return True
        elif len(pending) == 1:
            token, selected = next(iter(pending.items()))
        else:
            available = ", ".join(sorted(pending.keys()))
            await tools.send_message(
                "Multiple approvals pending. "
                f"Use `/{command} <id>`. Pending: {available}",
                mentions=mention,
            )
            return True

        if selected is None:
            raise RuntimeError("No matching pending approval after token lookup")

        is_session = command == "approve-session"
        # Session-level approval needs a non-empty key (e.g. a concrete command
        # string) so future requests can match.  Reject early so we never store
        # an empty string in _session_approved or report a misleading
        # "Future `` requests will be auto-approved" message to the user.
        if is_session and not selected.session_key:
            await tools.send_message(
                f"Approval `{token}` cannot be resolved as session-level: "
                "this request has no command signature to match against. "
                f"Use `/approve {token}` for a one-shot approval instead.",
                mentions=mention,
            )
            return True

        decision_value: ApprovalDecision
        if is_session:
            decision_value = "acceptForSession"
        elif command == "approve":
            decision_value = "accept"
        else:
            decision_value = "decline"
        if not selected.future.done():
            selected.future.set_result(decision_value)

        # Session-level: register the session key for auto-approval
        if is_session:
            self._record_session_approval(room_id, selected.session_key)
            await tools.send_message(
                f"Approval `{token}` resolved as `acceptForSession` (session-level). "
                f"Future `{selected.session_key}` requests will be auto-approved.",
                mentions=mention,
            )
        else:
            await tools.send_message(
                f"Approval `{token}` resolved as `{decision_value}`.",
                mentions=mention,
            )
        return True

    def _build_system_prompt(self) -> None:
        if self.config.system_prompt:
            self._system_prompt = self.config.system_prompt
            return

        self._system_prompt = render_system_prompt(
            agent_name=self.agent_name or "Agent",
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section,
            include_base_instructions=self.config.include_base_instructions,
            features=self.features,
        )

    def _apply_turn_overrides(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        params["model"] = self._selected_model
        params["cwd"] = self.config.cwd
        params["approvalPolicy"] = self.config.approval_policy
        params["personality"] = self.config.personality
        if self.config.reasoning_effort is not None:
            params["effort"] = self.config.reasoning_effort
        if self.config.reasoning_summary is not None:
            params["summary"] = self.config.reasoning_summary
        self._apply_turn_sandbox(params, room_id=room_id)

    async def _start_turn_with_model_fallback(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Start a turn, falling back to an available model if the auto-selected one is unavailable.

        Only attempts fallback when the model was auto-selected (config.model was None).
        When the user explicitly configured a model, the error propagates — they may
        be using unlisted models that model/list doesn't report.
        """
        if self._client is None:
            raise RuntimeError("CodexAdapter client is None — was on_started() called?")
        try:
            return await self._client.request("turn/start", params)
        except CodexJsonRpcError as exc:
            if self._model_explicitly_set:
                raise
            if not self._is_model_unavailable_error(exc):
                raise
            original_model = params.get("model", self._selected_model)
            logger.warning(
                "Model %s unavailable (code=%s): %s. Querying available models...",
                original_model,
                exc.code,
                exc.message,
            )
            fallback = await self._find_fallback_model(exclude=original_model)
            if fallback is None:
                raise
            logger.warning(
                "Falling back from %s to %s",
                original_model,
                fallback,
            )
            self._selected_model = fallback
            params["model"] = fallback
            return await self._client.request("turn/start", params)

    async def _find_fallback_model(self, exclude: Any = None) -> str | None:
        """Query model/list and return a fallback model, or None if unavailable."""
        if self._client is None:
            raise RuntimeError("CodexAdapter client is None — was on_started() called?")
        fallbacks = self.config.fallback_models
        try:
            result = await self._client.request("model/list", {})
        except Exception:
            logger.warning("model/list failed during fallback lookup", exc_info=True)
            for model_id in fallbacks:
                if model_id != exclude:
                    return model_id
            return None
        models = self._visible_model_ids(result)
        # Prefer configured fallback models if available
        for model_id in models:
            if model_id != exclude and model_id in fallbacks:
                return model_id
        for model_id in models:
            if model_id != exclude:
                return model_id
        # No visible models — try configured defaults
        for model_id in fallbacks:
            if model_id != exclude:
                return model_id
        return None

    # Known structured error type tags that indicate the model is not
    # available.  Codex's codexErrorInfo.type is the authoritative signal
    # when present; message substrings are a fragile fallback for servers
    # that don't yet emit structured error info.
    _MODEL_UNAVAILABLE_ERROR_TYPES: frozenset[str] = frozenset(
        {
            "ModelNotFound",
            "ModelNotAvailable",
            "ModelUnavailable",
            "Unauthorized",  # "no access to model X"
        }
    )
    _MODEL_UNAVAILABLE_PHRASES: tuple[str, ...] = (
        "model not found",
        "model not available",
        "is not available",
        "model_not_found",
        "model unavailable",
        "does not have access",
        "no access to model",
    )

    @classmethod
    def _is_model_unavailable_error(cls, exc: CodexJsonRpcError) -> bool:
        """Check if the error indicates the requested model is not available.

        Prefers structured ``codexErrorInfo.type`` signals on ``exc.data``
        when available; falls back to substring matching on the message for
        server versions that don't yet emit structured error info.
        """
        data = exc.data if isinstance(exc.data, dict) else None
        if data is not None:
            codex_info = data.get("codexErrorInfo")
            if isinstance(codex_info, dict):
                err_type = codex_info.get("type")
                if (
                    isinstance(err_type, str)
                    and err_type in cls._MODEL_UNAVAILABLE_ERROR_TYPES
                ):
                    return True
                # HTTP 404 on turn/start with any model-shaped error.
                if codex_info.get("httpStatus") == 404:
                    return True

        msg = exc.message.lower()
        return any(phrase in msg for phrase in cls._MODEL_UNAVAILABLE_PHRASES)

    def _apply_thread_sandbox(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        """Apply sandbox to thread/start params (only SandboxMode is accepted)."""
        if self.config.sandbox_policy is not None:
            # thread/start only has a `sandbox` field (SandboxMode enum).
            # Extract mode from sandbox_policy when possible.
            policy_type = self.config.sandbox_policy.get("type")
            if isinstance(policy_type, str):
                mode = self._normalize_sandbox_mode(policy_type)
                if mode is not None:
                    params["sandbox"] = mode
                    return
            logger.warning(
                "sandbox_policy type %s is not representable on thread/start; "
                "it will be applied at turn level instead",
                policy_type,
            )
            return

        effective = self._effective_sandbox(room_id)
        if effective is None:
            return

        sandbox_mode = self._normalize_sandbox_mode(effective)
        if sandbox_mode is not None:
            params["sandbox"] = sandbox_mode
            return

        if self._canonical_sandbox_key(effective) == "external-sandbox":
            # externalSandbox is only representable via sandboxPolicy on
            # turn/start; thread/start does not accept it.
            logger.debug(
                "external-sandbox will be applied at turn level, not on thread/start"
            )
            return

        logger.warning("Ignoring unsupported Codex sandbox value: %s", effective)

    # Codex app-server has two sandbox fields with different wire formats:
    #   - thread/start.sandbox: SandboxMode enum, kebab-case strings
    #     ("read-only", "workspace-write", "danger-full-access").
    #   - turn/start.sandboxPolicy: SandboxPolicy tagged union, camelCase
    #     type tags ("readOnly", "workspaceWrite", "dangerFullAccess",
    #     "externalSandbox").
    # This mapping bridges the two.  If the Codex protocol renames tags,
    # update both this mapping and _canonical_sandbox_key's aliases.
    # Reference: codex-app-server protocol types (thread/start, turn/start).
    _SANDBOX_MODE_TO_POLICY_TYPE: dict[str, str] = {
        "read-only": "readOnly",
        "workspace-write": "workspaceWrite",
        "danger-full-access": "dangerFullAccess",
    }

    def _apply_turn_sandbox(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        """Apply sandbox to turn/start params (full SandboxPolicy is accepted)."""
        if self.config.sandbox_policy is not None:
            params["sandboxPolicy"] = self._normalize_sandbox_policy(
                self.config.sandbox_policy
            )
            return

        effective = self._effective_sandbox(room_id)
        if effective is None:
            return

        sandbox_mode = self._normalize_sandbox_mode(effective)
        if sandbox_mode is not None:
            policy_type = self._SANDBOX_MODE_TO_POLICY_TYPE.get(sandbox_mode)
            if policy_type:
                params["sandboxPolicy"] = {"type": policy_type}
            return

        if self._canonical_sandbox_key(effective) == "external-sandbox":
            params["sandboxPolicy"] = {"type": "externalSandbox"}
            return

        logger.warning("Ignoring unsupported Codex sandbox value: %s", effective)

    def _effective_sandbox(self, room_id: str | None = None) -> str | None:
        """Return the effective sandbox for *room_id*, checking per-room overrides first."""
        if room_id:
            override = self._sandbox_overrides.get(room_id)
            if override is not None:
                return override
        return self.config.sandbox

    @classmethod
    def _normalize_sandbox_mode(cls, sandbox: str) -> str | None:
        key = cls._canonical_sandbox_key(sandbox)
        if key in {"read-only", "workspace-write", "danger-full-access"}:
            return key
        return None

    @classmethod
    def _normalize_sandbox_policy(
        cls, sandbox_policy: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = dict(sandbox_policy)
        policy_type = normalized.get("type")
        if not isinstance(policy_type, str):
            return normalized

        key = cls._canonical_sandbox_key(policy_type)
        camel = cls._SANDBOX_MODE_TO_POLICY_TYPE.get(key)
        if camel:
            normalized["type"] = camel
        elif key == "external-sandbox":
            # externalSandbox is represented only via sandboxPolicy.
            normalized["type"] = "externalSandbox"
        return normalized

    @staticmethod
    def _canonical_sandbox_key(value: str) -> str:
        compact = value.strip().lower().replace("_", "-").replace(" ", "")
        aliases = {
            "readonly": "read-only",
            "read-only": "read-only",
            "workspacewrite": "workspace-write",
            "workspace-write": "workspace-write",
            "dangerfullaccess": "danger-full-access",
            "danger-full-access": "danger-full-access",
            "externalsandbox": "external-sandbox",
            "external-sandbox": "external-sandbox",
        }
        return aliases.get(compact, compact)

    @staticmethod
    def _extract_turn_error(turn_payload: dict[str, Any]) -> str:
        error = turn_payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        return ""

    @staticmethod
    def _approval_summary(method: str, params: dict[str, Any]) -> str:
        if method == "item/commandExecution/requestApproval":
            command = params.get("command")
            if isinstance(command, str) and command:
                return f"command: {command}"
            return "command execution"
        if method == "item/fileChange/requestApproval":
            reason = params.get("reason")
            if isinstance(reason, str) and reason:
                return f"file changes: {reason}"
            return "file changes"
        return method

    @staticmethod
    def _approval_type(method: str) -> str:
        """Return a short label for the approval request type."""
        if method == "item/commandExecution/requestApproval":
            return "commandExecution"
        if method == "item/fileChange/requestApproval":
            return "fileChange"
        return method

    def _session_approval_key(self, method: str, params: dict[str, Any]) -> str:
        """Build a key for session-level auto-approval matching.

        When ``session_approval_granularity`` is ``"full_command"`` (default),
        the key includes the full command string so ``/approve-session`` on
        ``npm test`` only auto-approves future ``npm test`` requests.

        When set to ``"binary"``, keys on the command binary (first token) so
        ``/approve-session`` on ``npm test`` auto-approves all ``npm`` commands.

        For file changes, keys on the set of paths being modified so
        ``/approve-session`` on a request touching ``src/foo.py`` only
        auto-approves future requests touching the same path(s).  When no
        paths are present we return an empty string so session-level approval
        is refused — keying on the bare method string would turn
        ``/approve-session`` into a blanket "approve every future file change"
        switch, which is a security footgun.
        """
        if method == "item/commandExecution/requestApproval":
            command = params.get("command")
            if isinstance(command, str) and command.strip():
                cmd = command.strip()
                if self.config.session_approval_granularity == "binary":
                    return f"commandExecution:{cmd.split()[0]}"
                return f"commandExecution:{cmd}"
            # No identifiable command — return empty so session-level approval
            # is not possible (avoids a blanket wildcard match).
            return ""

        if method == "item/fileChange/requestApproval":
            paths = self._extract_file_change_paths(params)
            if not paths:
                # No identifiable paths — refuse session-level approval so
                # one /approve-session can't auto-approve every future file
                # change in this room.
                return ""
            # Sort for a stable key regardless of change order.
            return "fileChange:" + "|".join(sorted(paths))

        # Unknown method — refuse session-level approval rather than key on
        # the bare method string (which would collapse all future requests
        # of this method into one bucket).
        return ""

    @staticmethod
    def _extract_file_change_paths(params: dict[str, Any]) -> list[str]:
        """Extract file paths from an item/fileChange/requestApproval payload."""
        paths: list[str] = []
        changes = params.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    path = change.get("path")
                    if isinstance(path, str) and path:
                        paths.append(path)
        # Some payload variants carry paths at the top level.
        for key in ("path", "paths"):
            value = params.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
            elif isinstance(value, list):
                for p in value:
                    if isinstance(p, str) and p:
                        paths.append(p)
        return paths

    @staticmethod
    def _task_event_id(params: dict[str, Any]) -> str | None:
        for key in ("taskId", "task_id"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value

        task_value = params.get("task")
        if isinstance(task_value, dict):
            for key in ("taskId", "task_id", "id"):
                nested = task_value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
        return None

    @staticmethod
    def _task_event_title(params: dict[str, Any]) -> str | None:
        for key in ("title", "name"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value

        task_value = params.get("task")
        if isinstance(task_value, str) and task_value:
            return task_value
        if isinstance(task_value, dict):
            for key in ("title", "name", "description"):
                nested = task_value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
        return None

    @staticmethod
    def _task_event_summary(params: dict[str, Any]) -> str | None:
        for key in ("summary", "result", "message", "description"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value

        task_value = params.get("task")
        if isinstance(task_value, dict):
            for key in ("summary", "result", "message", "description"):
                nested = task_value.get(key)
                if isinstance(nested, str) and nested:
                    return nested
        return None

    @staticmethod
    def _build_task_event_content(
        *,
        task_id: str | None,
        task: str,
        status: str,
        summary: str | None = None,
    ) -> str:
        lines: list[str] = []
        if task_id:
            lines.append(f"UUID: {task_id}")
        lines.append(f"Task: {task}")
        lines.append(f"Status: {status}")
        if summary and summary != task:
            lines.append(f"Summary: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _extract_local_command(content: str) -> tuple[str, str] | None:
        """Return ``(command, args)`` when ``content`` opens with a slash command.

        Scans only the first ``_COMMAND_TOKEN_SEARCH_LIMIT`` tokens so the
        platform's leading mention block (``@handle DisplayName`` per
        participant, plus the occasional bare display name) can't bury a
        legitimate ``/command``, while a slash word used as prose in the
        body of a longer message still reads as prose.
        """
        tokens = content.strip().split()
        if not tokens:
            return None
        search_limit = min(len(tokens), _COMMAND_TOKEN_SEARCH_LIMIT)
        for idx in range(search_limit):
            token = tokens[idx]
            if not token.startswith("/") or len(token) == 1:
                continue
            command = token[1:].lower()
            if command not in _LOCAL_COMMANDS:
                continue
            args = " ".join(tokens[idx + 1 :]).strip()
            return command, args
        return None

    @staticmethod
    def _approval_token(request_id: int | str, params: dict[str, Any]) -> str:
        for key in ("approvalId", "approval_id", "itemId", "callId"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value
        return f"req-{request_id}"

    def _clear_pending_approval(self, room_id: str, token: str) -> None:
        room_pending = self._pending_approvals.get(room_id)
        if not room_pending:
            return
        room_pending.pop(token, None)
        if not room_pending:
            self._pending_approvals.pop(room_id, None)

    def _clear_pending_approvals_for_room(self, room_id: str) -> None:
        room_pending = self._pending_approvals.pop(room_id, {})
        for item in room_pending.values():
            if not item.future.done():
                item.future.set_result("decline")

    @staticmethod
    def _visible_model_ids(result: dict[str, Any]) -> list[str]:
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            return []
        models: list[str] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            if bool(entry.get("hidden", False)):
                continue
            models.append(model_id)
        return models
