"""OpenCode server adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Any, Literal

import httpx

from thenvoi.converters._utils import optional_str
from thenvoi.converters.opencode import OpencodeHistoryConverter
from thenvoi.core.exceptions import ThenvoiConfigError
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.integrations.mcp.backends import (
    ThenvoiMCPBackend,
    create_thenvoi_mcp_backend,
)
from thenvoi.integrations.opencode import (
    HttpOpencodeClient,
    OpencodeClientProtocol,
    OpencodeSessionState,
)
from thenvoi.runtime.custom_tools import CustomToolDef
from thenvoi.runtime.prompts import render_system_prompt
from thenvoi.runtime.tools import iter_tool_definitions

logger = logging.getLogger(__name__)

ApprovalMode = Literal["manual", "auto_accept", "auto_decline"]
QuestionMode = Literal["manual", "auto_reject"]
ApprovalReply = Literal["once", "always", "reject"]

_OPENCODE_SYSTEM_NOTE = """\
Responses are relayed back into the Thenvoi room by the adapter.
Use the thenvoi_ prefixed tools (e.g. thenvoi_send_message) for Thenvoi platform actions when available.
When you need approval or clarification, ask clearly and wait for the user's next room message.
"""


@dataclass
class _PendingPermission:
    request_id: str
    permission: str
    patterns: list[str]
    timeout_task: asyncio.Task[None] | None = None


@dataclass
class _PendingQuestion:
    request_id: str
    questions: list[dict[str, Any]]
    timeout_task: asyncio.Task[None] | None = None


@dataclass
class _RoomState:
    room_id: str
    session_id: str | None = None
    tools: AgentToolsProtocol | None = None
    turn_future: asyncio.Future[None] | None = None
    turn_release_future: asyncio.Future[None] | None = None
    turn_task: asyncio.Task[None] | None = None
    pending_mentions: list[dict[str, str]] = field(default_factory=list)
    text_parts: OrderedDict[str, str] = field(default_factory=OrderedDict)
    assistant_message_ids: set[str] = field(default_factory=set)
    assistant_part_types: dict[str, str] = field(default_factory=dict)
    reported_tool_calls: set[str] = field(default_factory=set)
    reported_tool_results: set[str] = field(default_factory=set)
    pending_permission: _PendingPermission | None = None
    pending_question: _PendingQuestion | None = None
    last_error_message: str | None = None
    persisted_session_id: str | None = None


@dataclass
class OpencodeAdapterConfig:
    """Runtime configuration for OpenCode sessions."""

    base_url: str = "http://127.0.0.1:4096"
    directory: str | None = None
    workspace: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    agent: str | None = None
    variant: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = False
    enable_task_events: bool = True
    enable_execution_reporting: bool = False
    enable_memory_tools: bool = False
    fallback_send_agent_text: bool = True
    turn_timeout_s: float = 300.0
    approval_mode: ApprovalMode = "manual"
    approval_wait_timeout_s: float = 300.0
    approval_timeout_reply: ApprovalReply = "reject"
    question_mode: QuestionMode = "manual"
    question_wait_timeout_s: float = 300.0
    session_title_prefix: str = "Thenvoi"
    mcp_server_name: str = "thenvoi"


class OpencodeAdapter(SimpleAdapter[OpencodeSessionState]):
    """Thenvoi adapter for the OpenCode HTTP server.

    Maps each Thenvoi room to an OpenCode session. Messages from the room
    are forwarded as prompts; SSE events from OpenCode are relayed back as
    room messages, tool-call/result reports, and error events. Platform
    tools come from thenvoi-mcp, while `additional_tools` are exposed through
    a separate local MCP server.

    Approval lifecycle (``approval_mode``):
      * ``manual`` -- permission prompts are forwarded to the room; the user
        replies with ``approve``, ``always``, or ``reject`` before a
        configurable timeout (``approval_wait_timeout_s``).
      * ``auto_accept`` -- every permission is approved with ``once``.
      * ``auto_decline`` -- every permission is rejected immediately.

    Question lifecycle (``question_mode``):
      * ``manual`` -- questions are forwarded to the room; the user replies
        with answers or ``reject`` before ``question_wait_timeout_s``.
      * ``auto_reject`` -- questions are rejected immediately.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.TASK_EVENTS}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS}
    )

    def __init__(
        self,
        config: OpencodeAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        history_converter: OpencodeHistoryConverter | None = None,
        client_factory: Callable[[OpencodeAdapterConfig], OpencodeClientProtocol]
        | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        self._config = config or OpencodeAdapterConfig()

        # Detect non-default legacy booleans (enable_task_events defaults to
        # True, so only enable_memory_tools and enable_execution_reporting
        # count as "legacy usage").
        _has_legacy_booleans = (
            self._config.enable_memory_tools or self._config.enable_execution_reporting
        )

        if _has_legacy_booleans and features is not None:
            raise ThenvoiConfigError(
                "Cannot pass both legacy boolean flags in OpencodeAdapterConfig "
                "(enable_memory_tools / enable_execution_reporting) "
                "and 'features'. "
                "Use features=AdapterFeatures(...) instead."
            )

        # Build features from config booleans when not explicitly provided.
        if features is None:
            if _has_legacy_booleans:
                warnings.warn(
                    "enable_memory_tools and enable_execution_reporting in "
                    "OpencodeAdapterConfig are deprecated. "
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
            history_converter=history_converter or OpencodeHistoryConverter(),
            features=features,
        )
        self.config = self._config
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        self._client_factory = client_factory or self._default_client_factory
        self._client: OpencodeClientProtocol | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._mcp_backend: ThenvoiMCPBackend | None = None
        self._rooms: dict[str, _RoomState] = {}
        self._room_by_session: dict[str, str] = {}
        self._state_lock = asyncio.Lock()
        self._system_prompt: str = ""

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)

        self._system_prompt = render_system_prompt(
            agent_name=agent_name,
            agent_description=agent_description,
            custom_section=self.config.custom_section,
            include_base_instructions=self.config.include_base_instructions,
            features=self.features,
        ).strip()
        self._system_prompt = (
            f"{self._system_prompt}\n\n{_OPENCODE_SYSTEM_NOTE}".strip()
        )

        self._log_startup_config(agent_name)

    def _log_startup_config(self, agent_name: str) -> None:
        logger.info(
            "OpenCode adapter started: agent=%s, base_url=%s, "
            "provider=%s, model=%s, approval_mode=%s, "
            "question_mode=%s, execution_reporting=%s, "
            "task_events=%s, mcp_server=%s, custom_tools=%d",
            agent_name,
            self.config.base_url,
            self.config.provider_id or "default",
            self.config.model_id or "default",
            self.config.approval_mode,
            self.config.question_mode,
            self.config.enable_execution_reporting,
            self.config.enable_task_events,
            self.config.mcp_server_name,
            len(self._custom_tools),
        )

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: OpencodeSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        room_state = await self._get_or_create_room_state(room_id)
        room_state.tools = tools

        if await self._handle_control_message(room_state, msg):
            return

        if room_state.turn_future and not room_state.turn_future.done():
            await tools.send_event(
                "OpenCode is still processing the previous request in this room.",
                "error",
            )
            return

        await self._ensure_client_started()
        client = self._client
        if client is None:
            raise RuntimeError("OpenCode client is not initialized")

        try:
            session_id, created, restored_missing_session = await self._ensure_session(
                room_state, history
            )
            if Emit.TASK_EVENTS in self.features.emit and (
                room_state.persisted_session_id != session_id or is_session_bootstrap
            ):
                await self._emit_session_task_event(
                    room_state,
                    status="created" if created else "resumed",
                )

            self._begin_turn(room_state, sender_id=msg.sender_id)
            try:
                await client.prompt_async(
                    session_id,
                    parts=self._build_prompt_parts(
                        msg,
                        participants_msg,
                        contacts_msg,
                        replay_messages=(
                            history.replay_messages
                            if restored_missing_session
                            else None
                        ),
                    ),
                    system=self._system_prompt,
                    model=self._build_model_payload(),
                    agent=self.config.agent,
                    variant=self.config.variant,
                )
            except Exception:
                self._clear_turn_state(room_state)
                raise

            release_future = room_state.turn_release_future
            turn_future = room_state.turn_future
            turn_task = asyncio.create_task(
                self._watch_turn_completion(room_state, room_id, turn_future)
            )
            room_state.turn_task = turn_task

            if release_future is not None:
                await release_future
            if turn_future is not None and turn_future.done():
                await turn_task
        except asyncio.TimeoutError:
            logger.warning(
                "OpenCode turn timed out for room %s (session=%s)",
                room_id,
                room_state.session_id,
            )
            if self._client and room_state.session_id:
                try:
                    await self._client.abort_session(room_state.session_id)
                except Exception:
                    logger.exception(
                        "Failed to abort timed-out OpenCode session %s",
                        room_state.session_id,
                    )
            await tools.send_event(
                "OpenCode timed out before completing the turn.",
                "error",
            )
        except httpx.HTTPStatusError as exc:
            logger.exception("OpenCode request failed for room %s", room_id)
            await tools.send_event(
                self._format_http_error(exc),
                "error",
            )
        except Exception:
            logger.exception("Unexpected OpenCode adapter failure in room %s", room_id)
            await tools.send_event(
                "OpenCode failed while processing the message.",
                "error",
            )

    async def on_cleanup(self, room_id: str) -> None:
        room_state: _RoomState | None = None
        should_shutdown = False

        async with self._state_lock:
            room_state = self._rooms.pop(room_id, None)
            if room_state and room_state.session_id:
                self._room_by_session.pop(room_state.session_id, None)
            should_shutdown = not self._rooms

        if room_state:
            self._clear_turn_state(room_state)

        if should_shutdown:
            await self._shutdown_client()

    def _default_client_factory(
        self, config: OpencodeAdapterConfig
    ) -> OpencodeClientProtocol:
        return HttpOpencodeClient(
            base_url=config.base_url,
            directory=config.directory,
            workspace=config.workspace,
            timeout_s=config.turn_timeout_s,
        )

    def _get_room_tools(self, room_id: str) -> AgentToolsProtocol | None:
        """Resolve room-scoped tools for the shared MCP backend."""
        state = self._rooms.get(room_id)
        return state.tools if state else None

    async def _get_or_create_room_state(self, room_id: str) -> _RoomState:
        async with self._state_lock:
            state = self._rooms.get(room_id)
            if state is None:
                state = _RoomState(room_id=room_id)
                self._rooms[room_id] = state
            return state

    async def _ensure_client_started(self) -> None:
        async with self._state_lock:
            was_new = self._client is None
            if self._client is None:
                self._client = self._client_factory(self.config)
            if self._event_task is None or self._event_task.done():
                self._event_task = asyncio.create_task(self._run_event_loop())

        if was_new:
            await self._register_mcp_backend()

    async def _ensure_mcp_backend(self) -> ThenvoiMCPBackend:
        """Create the shared Thenvoi MCP backend (LocalMCPServer with SSE)."""
        if self._mcp_backend is not None:
            return self._mcp_backend

        tool_definitions = list(
            iter_tool_definitions(
                include_memory=Capability.MEMORY in self.features.capabilities,
                include_contacts=Capability.CONTACTS in self.features.capabilities,
            )
        )
        backend = await create_thenvoi_mcp_backend(
            kind="sse",
            tool_definitions=tool_definitions,
            get_tools=self._get_room_tools,
            additional_tools=self._custom_tools or None,
        )
        # Re-check after await: _shutdown_client may have cleared _mcp_backend
        if self._mcp_backend is not None:
            await backend.stop()
            return self._mcp_backend
        self._mcp_backend = backend
        logger.info(
            "Shared Thenvoi MCP backend started with %d tools (%d custom)",
            len(backend.allowed_tools),
            len(self._custom_tools),
        )
        return backend

    async def _register_mcp_backend(self) -> None:
        """Start the shared MCP backend and register it with OpenCode."""
        if self._client is None:
            return

        try:
            backend = await self._ensure_mcp_backend()
        except Exception:
            logger.exception("Failed to start shared Thenvoi MCP backend for OpenCode")
            return

        local_server = backend.local_server
        if local_server is None:
            logger.warning("MCP backend has no local server to register with OpenCode")
            return

        try:
            await self._client.register_mcp_server(
                name=self.config.mcp_server_name,
                url=local_server.sse_url,
            )
            logger.info(
                "Registered MCP server %s at %s with OpenCode",
                self.config.mcp_server_name,
                local_server.sse_url,
            )
        except Exception:
            logger.exception(
                "Failed to register MCP server %s with OpenCode",
                self.config.mcp_server_name,
            )

    async def _shutdown_client(self) -> None:
        async with self._state_lock:
            event_task = self._event_task
            client = self._client
            mcp_backend = self._mcp_backend
            self._event_task = None
            self._client = None
            self._mcp_backend = None

        if mcp_backend is not None:
            if client is not None:
                try:
                    await client.deregister_mcp_server(self.config.mcp_server_name)
                except Exception:
                    logger.debug(
                        "Failed to deregister MCP server %s (OpenCode may already be stopped)",
                        self.config.mcp_server_name,
                    )
            await mcp_backend.stop()

        if event_task is not None:
            event_task.cancel()
            try:
                await event_task
            except asyncio.CancelledError:
                pass

        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.exception("Failed to close OpenCode client")

    async def _run_event_loop(self) -> None:
        retry_delay = 1.0
        max_retry_delay = 30.0

        while self._client is not None:
            try:
                client = self._client
                if client is None:
                    return
                async for event in client.iter_events():
                    retry_delay = 1.0  # reset on successful event
                    await self._handle_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "OpenCode event stream failed; retrying in %.1fs", retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            else:
                await asyncio.sleep(0.25)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        properties = event.get("properties") or {}
        if not isinstance(properties, dict):
            return

        room_state = await self._room_state_for_event(event_type, properties)
        if room_state is None:
            return

        if event_type == "message.updated":
            info = properties.get("info") or {}
            if isinstance(info, dict):
                message_id = optional_str(info.get("id"))
                if info.get("role") == "assistant":
                    if message_id:
                        room_state.assistant_message_ids.add(message_id)
                    error = info.get("error")
                    if error:
                        room_state.last_error_message = self._format_opencode_error(
                            error
                        )
            return

        if event_type == "message.part.updated":
            part = properties.get("part") or {}
            if isinstance(part, dict):
                await self._handle_part_update(room_state, part)
            return

        if event_type == "message.part.delta":
            await self._handle_part_delta(room_state, properties)
            return

        if event_type == "permission.asked":
            await self._handle_permission_asked(room_state, properties)
            return

        if event_type == "question.asked":
            await self._handle_question_asked(room_state, properties)
            return

        if event_type == "session.error":
            room_state.last_error_message = self._format_opencode_error(
                properties.get("error")
            )
            self._finish_turn(room_state)
            return

        if event_type == "session.idle":
            self._finish_turn(room_state)

    async def _room_state_for_event(
        self, event_type: str, properties: dict[str, Any]
    ) -> _RoomState | None:
        session_id: str | None = None
        if "sessionID" in properties:
            session_id = optional_str(properties.get("sessionID"))
        elif event_type == "message.updated":
            info = properties.get("info") or {}
            if isinstance(info, dict):
                session_id = optional_str(info.get("sessionID"))
        elif event_type == "message.part.updated":
            part = properties.get("part") or {}
            if isinstance(part, dict):
                session_id = optional_str(part.get("sessionID"))

        if not session_id:
            return None

        async with self._state_lock:
            room_id = self._room_by_session.get(session_id)
            if not room_id:
                return None
            return self._rooms.get(room_id)

    async def _handle_part_update(
        self, room_state: _RoomState, part: dict[str, Any]
    ) -> None:
        part_type = part.get("type")
        part_id = optional_str(part.get("id"))
        message_id = optional_str(part.get("messageID"))
        if not part_id:
            return

        if part_type == "text":
            if not message_id or message_id not in room_state.assistant_message_ids:
                return
            room_state.assistant_part_types[part_id] = "text"
            room_state.text_parts[part_id] = str(part.get("text") or "")
            return

        if part_type == "reasoning":
            if not message_id or message_id not in room_state.assistant_message_ids:
                return
            room_state.assistant_part_types[part_id] = "reasoning"
            return

        if part_type != "tool" or Emit.EXECUTION not in self.features.emit:
            return

        state = part.get("state") or {}
        if not isinstance(state, dict):
            return

        tool_name = optional_str(part.get("tool")) or "unknown"
        call_id = optional_str(part.get("callID")) or part_id
        if state.get("status") in {"pending", "running"}:
            if call_id not in room_state.reported_tool_calls:
                room_state.reported_tool_calls.add(call_id)
                await self._report_tool_call(room_state, tool_name, state, call_id)
            return

        if state.get("status") in {"completed", "error"}:
            if call_id not in room_state.reported_tool_calls:
                room_state.reported_tool_calls.add(call_id)
                await self._report_tool_call(room_state, tool_name, state, call_id)
            if call_id not in room_state.reported_tool_results:
                room_state.reported_tool_results.add(call_id)
                await self._report_tool_result(room_state, state, call_id)

    async def _handle_part_delta(
        self, room_state: _RoomState, properties: dict[str, Any]
    ) -> None:
        if properties.get("field") != "text":
            return
        part_id = optional_str(properties.get("partID"))
        message_id = optional_str(properties.get("messageID"))
        if not part_id:
            return
        if not message_id or message_id not in room_state.assistant_message_ids:
            return
        if room_state.assistant_part_types.get(part_id) != "text":
            return
        delta = str(properties.get("delta") or "")
        room_state.text_parts[part_id] = room_state.text_parts.get(part_id, "") + delta

    async def _handle_permission_asked(
        self, room_state: _RoomState, properties: dict[str, Any]
    ) -> None:
        request_id = optional_str(properties.get("id"))
        if not request_id:
            return

        pending = _PendingPermission(
            request_id=request_id,
            permission=optional_str(properties.get("permission")) or "unknown",
            patterns=[
                str(pattern)
                for pattern in properties.get("patterns") or []
                if pattern is not None
            ],
        )
        self._cancel_pending_timeout(room_state.pending_permission)
        room_state.pending_permission = pending

        if self.config.approval_mode == "auto_accept":
            await self._reply_permission(room_state, "once")
            return

        if self.config.approval_mode == "auto_decline":
            await self._reply_permission(room_state, "reject")
            return

        pending.timeout_task = asyncio.create_task(
            self._expire_permission(room_state, request_id)
        )
        if room_state.tools:
            pattern_text = ", ".join(pending.patterns) if pending.patterns else "n/a"
            await room_state.tools.send_message(
                (
                    f"OpenCode approval requested for `{pending.permission}` "
                    f"({pattern_text}). Reply with `approve {request_id}`, "
                    f"`always {request_id}`, or `reject {request_id}`."
                )
            )
        self._release_turn_wait(room_state)

    async def _handle_question_asked(
        self, room_state: _RoomState, properties: dict[str, Any]
    ) -> None:
        request_id = optional_str(properties.get("id"))
        questions = properties.get("questions") or []
        if not request_id or not isinstance(questions, list):
            return

        pending = _PendingQuestion(
            request_id=request_id,
            questions=[q for q in questions if isinstance(q, dict)],
        )
        self._cancel_pending_timeout(room_state.pending_question)
        room_state.pending_question = pending

        if self.config.question_mode == "auto_reject":
            await self._reject_question(room_state)
            return

        pending.timeout_task = asyncio.create_task(
            self._expire_question(room_state, request_id)
        )
        if room_state.tools:
            prompt = self._format_question_prompt(pending.questions, request_id)
            await room_state.tools.send_message(prompt)
        self._release_turn_wait(room_state)

    async def _handle_control_message(
        self, room_state: _RoomState, msg: PlatformMessage
    ) -> bool:
        content = msg.content.strip()
        if not content:
            return False

        lowered = content.lower()

        if room_state.pending_permission:
            pending_request_id = room_state.pending_permission.request_id
            reply = self._parse_permission_reply(lowered, room_state.pending_permission)
            if reply:
                await self._reply_permission(room_state, reply)
                if room_state.tools:
                    await room_state.tools.send_message(
                        f"OpenCode approval `{pending_request_id}` handled with `{reply}`."
                    )
                return True

        if room_state.pending_question:
            pending_request_id = room_state.pending_question.request_id
            if lowered in {"reject", "/reject"}:
                await self._reject_question(room_state)
                if room_state.tools:
                    await room_state.tools.send_message(
                        f"OpenCode question `{pending_request_id}` rejected."
                    )
                return True

            answers = self._parse_question_answers(content, room_state.pending_question)
            if answers is None:
                if room_state.tools:
                    await room_state.tools.send_message(
                        (
                            "OpenCode is waiting for answers. Reply with one line per "
                            "question, or `reject` to reject the question."
                        )
                    )
                return True

            await self._reply_question(room_state, answers)
            if room_state.tools:
                await room_state.tools.send_message(
                    f"OpenCode question `{pending_request_id}` answered."
                )
            return True

        return False

    async def _reply_permission(
        self, room_state: _RoomState, reply: ApprovalReply
    ) -> None:
        pending = room_state.pending_permission
        if pending is None or self._client is None:
            return
        if not room_state.session_id:
            logger.warning(
                "Cannot reply to permission %s: no session_id for room %s",
                pending.request_id,
                room_state.room_id,
            )
            return
        self._cancel_pending_timeout(pending)
        await self._client.reply_permission(
            room_state.session_id,
            pending.request_id,
            response=reply,
        )
        room_state.pending_permission = None

    async def _reply_question(
        self, room_state: _RoomState, answers: list[list[str]]
    ) -> None:
        pending = room_state.pending_question
        if pending is None or self._client is None:
            return
        self._cancel_pending_timeout(pending)
        await self._client.reply_question(
            pending.request_id,
            answers=answers,
        )
        room_state.pending_question = None

    async def _reject_question(self, room_state: _RoomState) -> None:
        pending = room_state.pending_question
        if pending is None or self._client is None:
            return
        self._cancel_pending_timeout(pending)
        await self._client.reject_question(pending.request_id)
        room_state.pending_question = None

    async def _expire_permission(self, room_state: _RoomState, request_id: str) -> None:
        try:
            await asyncio.sleep(self.config.approval_wait_timeout_s)
        except asyncio.CancelledError:
            return

        if (
            room_state.pending_permission is not None
            and room_state.pending_permission.request_id == request_id
        ):
            await self._reply_permission(room_state, self.config.approval_timeout_reply)
            if room_state.tools:
                await room_state.tools.send_event(
                    f"OpenCode approval `{request_id}` timed out and was handled with `{self.config.approval_timeout_reply}`.",
                    "error",
                )

    async def _expire_question(self, room_state: _RoomState, request_id: str) -> None:
        try:
            await asyncio.sleep(self.config.question_wait_timeout_s)
        except asyncio.CancelledError:
            return

        if (
            room_state.pending_question is not None
            and room_state.pending_question.request_id == request_id
        ):
            await self._reject_question(room_state)
            if room_state.tools:
                await room_state.tools.send_event(
                    f"OpenCode question `{request_id}` timed out and was rejected.",
                    "error",
                )

    def _cancel_pending_timeout(
        self, pending: _PendingPermission | _PendingQuestion | None
    ) -> None:
        if pending and pending.timeout_task:
            pending.timeout_task.cancel()

    async def _ensure_session(
        self, room_state: _RoomState, history: OpencodeSessionState
    ) -> tuple[str, bool, bool]:
        if self._client is None:
            raise RuntimeError("OpenCode client is not initialized")

        restored_session_id = room_state.session_id or history.session_id
        created = False
        restored_missing_session = False

        if restored_session_id:
            try:
                session = await self._client.get_session(restored_session_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                logger.info(
                    "OpenCode session %s no longer exists; creating a new session",
                    restored_session_id,
                )
                session = await self._client.create_session(
                    title=self._build_session_title(room_state.room_id),
                )
                created = True
                restored_missing_session = True
            session_id = str(session["id"])
        else:
            session = await self._client.create_session(
                title=self._build_session_title(room_state.room_id),
            )
            session_id = str(session["id"])
            created = True

        async with self._state_lock:
            if room_state.session_id and room_state.session_id != session_id:
                self._room_by_session.pop(room_state.session_id, None)
            room_state.session_id = session_id
            self._room_by_session[session_id] = room_state.room_id

        return session_id, created, restored_missing_session

    def _begin_turn(self, room_state: _RoomState, *, sender_id: str | None) -> None:
        loop = asyncio.get_running_loop()
        room_state.turn_future = loop.create_future()
        room_state.turn_release_future = loop.create_future()
        room_state.turn_task = None
        room_state.pending_mentions = [{"id": sender_id}] if sender_id else []
        room_state.text_parts.clear()
        room_state.assistant_message_ids.clear()
        room_state.assistant_part_types.clear()
        room_state.reported_tool_calls.clear()
        room_state.reported_tool_results.clear()
        room_state.last_error_message = None

    async def _watch_turn_completion(
        self,
        room_state: _RoomState,
        room_id: str,
        turn_future: asyncio.Future[None] | None,
    ) -> None:
        if turn_future is None:
            return

        try:
            await asyncio.wait_for(turn_future, self.config.turn_timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "OpenCode turn timed out for room %s (session=%s)",
                room_id,
                room_state.session_id,
            )
            if self._client and room_state.session_id:
                try:
                    await self._client.abort_session(room_state.session_id)
                except Exception:
                    logger.exception(
                        "Failed to abort timed-out OpenCode session %s",
                        room_state.session_id,
                    )
            if room_state.tools:
                await room_state.tools.send_event(
                    "OpenCode timed out before completing the turn.",
                    "error",
                )
            self._release_turn_wait(room_state)
        else:
            await self._deliver_fallback_text(room_state)
            self._release_turn_wait(room_state)
        finally:
            self._clear_turn_state(
                room_state,
                expected_future=turn_future,
                expected_task=asyncio.current_task(),
            )

    def _release_turn_wait(self, room_state: _RoomState) -> None:
        self._resolve_future(room_state.turn_release_future)

    def _finish_turn(self, room_state: _RoomState) -> None:
        self._resolve_future(room_state.turn_future)
        self._resolve_future(room_state.turn_release_future)

    def _clear_turn_state(
        self,
        room_state: _RoomState,
        *,
        expected_future: asyncio.Future[None] | None = None,
        expected_task: asyncio.Task[None] | None = None,
    ) -> None:
        if (
            expected_future is not None
            and room_state.turn_future is not expected_future
        ):
            return

        turn_task = room_state.turn_task
        if turn_task is not None and turn_task is not expected_task:
            turn_task.cancel()

        self._cancel_pending_timeout(room_state.pending_permission)
        self._cancel_pending_timeout(room_state.pending_question)
        room_state.pending_permission = None
        room_state.pending_question = None
        room_state.turn_future = None
        room_state.turn_release_future = None
        room_state.turn_task = None

    @staticmethod
    def _resolve_future(future: asyncio.Future[None] | None) -> None:
        if future is not None and not future.done():
            future.set_result(None)

    async def _emit_session_task_event(
        self, room_state: _RoomState, *, status: str
    ) -> None:
        if room_state.tools is None or not room_state.session_id:
            return

        created_at = datetime.now(timezone.utc).isoformat()
        await room_state.tools.send_event(
            f"OpenCode session {status}: `{room_state.session_id}`",
            "task",
            metadata={
                "opencode_session_id": room_state.session_id,
                "opencode_room_id": room_state.room_id,
                "opencode_created_at": created_at,
            },
        )
        room_state.persisted_session_id = room_state.session_id

    async def _deliver_fallback_text(self, room_state: _RoomState) -> None:
        if room_state.tools is None or not self.config.fallback_send_agent_text:
            return

        text = "\n".join(
            part_text.strip()
            for part_text in room_state.text_parts.values()
            if part_text.strip()
        ).strip()

        if text:
            await room_state.tools.send_message(
                text,
                mentions=room_state.pending_mentions,
            )
            room_state.pending_mentions = []
            return

        if room_state.last_error_message:
            await room_state.tools.send_event(room_state.last_error_message, "error")
            room_state.pending_mentions = []
            return

        await room_state.tools.send_message(
            "OpenCode completed the turn without a text reply.",
            mentions=room_state.pending_mentions,
        )
        room_state.pending_mentions = []

    async def _report_tool_call(
        self,
        room_state: _RoomState,
        tool_name: str,
        state: dict[str, Any],
        call_id: str,
    ) -> None:
        if room_state.tools is None:
            return
        try:
            await room_state.tools.send_event(
                json.dumps(
                    {
                        "name": tool_name,
                        "args": state.get("input") or {},
                        "tool_call_id": call_id,
                    }
                ),
                "tool_call",
            )
        except Exception:
            logger.exception("Failed to report OpenCode tool_call for %s", call_id)

    async def _report_tool_result(
        self,
        room_state: _RoomState,
        state: dict[str, Any],
        call_id: str,
    ) -> None:
        if room_state.tools is None:
            return
        output: Any
        if state.get("status") == "error":
            output = {"error": state.get("error") or "OpenCode tool failed"}
        else:
            output = state["output"] if "output" in state else ""

        try:
            await room_state.tools.send_event(
                json.dumps(
                    {
                        "output": output,
                        "tool_call_id": call_id,
                    }
                ),
                "tool_result",
            )
        except Exception:
            logger.exception("Failed to report OpenCode tool_result for %s", call_id)

    def _build_session_title(self, room_id: str) -> str:
        return f"{self.config.session_title_prefix}: {self.agent_name or 'Agent'} / {room_id}"

    def _build_model_payload(self) -> dict[str, str] | None:
        if not self.config.provider_id or not self.config.model_id:
            return None
        return {
            "providerID": self.config.provider_id,
            "modelID": self.config.model_id,
        }

    def _build_prompt_parts(
        self,
        msg: PlatformMessage,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        replay_messages: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        lines: list[str] = []
        if replay_messages:
            lines.append(
                "Previous OpenCode session state was missing. Recovered room history:"
            )
            lines.extend(replay_messages)
        if participants_msg:
            lines.append(f"[System]: {participants_msg}")
        if contacts_msg:
            lines.append(f"[System]: {contacts_msg}")

        sender_name = msg.sender_name or "Unknown"
        lines.append(f"[{sender_name}]: {msg.content}")
        return [{"type": "text", "text": "\n".join(lines)}]

    def _parse_permission_reply(
        self,
        lowered_content: str,
        pending: _PendingPermission,
    ) -> ApprovalReply | None:
        tokens = lowered_content.split()
        if not tokens:
            return None

        command = tokens[0].lstrip("/")
        request_id = tokens[1] if len(tokens) > 1 else pending.request_id
        if request_id != pending.request_id:
            return None

        if command == "approve":
            return "once"
        if command == "always":
            return "always"
        if command == "reject":
            return "reject"
        return None

    def _parse_question_answers(
        self, content: str, pending: _PendingQuestion
    ) -> list[list[str]] | None:
        if len(pending.questions) == 1:
            return [[content.strip()]]

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) < len(pending.questions):
            return None
        return [[line] for line in lines[: len(pending.questions)]]

    def _format_question_prompt(
        self, questions: list[dict[str, Any]], request_id: str
    ) -> str:
        prompt_lines = [f"OpenCode asked question `{request_id}`:"]
        for index, question in enumerate(questions, start=1):
            question_text = str(question.get("question") or "Question")
            prompt_lines.append(f"{index}. {question_text}")
        prompt_lines.append("Reply with one line per question, or `reject`.")
        return "\n".join(prompt_lines)

    def _format_http_error(self, exc: httpx.HTTPStatusError) -> str:
        try:
            payload = exc.response.json()
        except ValueError:
            payload = exc.response.text
        return f"OpenCode request failed ({exc.response.status_code}): {payload}"

    def _format_opencode_error(self, error: Any) -> str:
        if not isinstance(error, dict):
            return "OpenCode reported an unknown error."

        name = error.get("name") or "OpenCodeError"
        data = error.get("data") or {}
        if isinstance(data, dict):
            message = data.get("message")
            if message:
                return f"{name}: {message}"
        return f"{name}: OpenCode reported an error."
