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
from typing import ClassVar, Any

import httpx

from band.adapters.opencode.approvals import ApprovalPorts, RoomApprovals
from band.adapters.opencode.config import OpencodeAdapterConfig
from band.converters.opencode import OpencodeHistoryConverter
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
from band.integrations.mcp.backends import (
    BandMCPBackend,
    create_band_mcp_backend,
)
from band.integrations.opencode import (
    HttpOpencodeClient,
    MessagePartDeltaEvent,
    MessagePartUpdatedEvent,
    MessageUpdatedEvent,
    OpencodeClientProtocol,
    OpencodeEvent,
    OpencodeMessageInfo,
    OpencodePart,
    OpencodeSessionState,
    OpencodeToolState,
    PermissionAskedEvent,
    QuestionAskedEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    describe_error,
    parse_opencode_event,
)
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import iter_tool_definitions

logger = logging.getLogger(__name__)

_OPENCODE_SYSTEM_NOTE = """\
Responses are relayed back into the Band room by the adapter.
Use the band_ prefixed tools (e.g. band_send_message) for Band platform actions when available.
When you need approval or clarification, ask clearly and wait for the user's next room message.
"""


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
    # Bound in _get_or_create_room_state, immediately after construction.
    approvals: RoomApprovals = field(init=False)
    last_error_message: str | None = None
    persisted_session_id: str | None = None
    # Per-assistant-message usage for the current turn (last-write-wins per id,
    # since message.updated streams repeatedly). Summed across messages at turn
    # end — a tool loop produces several assistant messages.
    usage_by_message: dict[str, TurnUsage] = field(default_factory=dict)


class OpencodeAdapter(SimpleAdapter[OpencodeSessionState]):
    """Band adapter for the OpenCode HTTP server.

    Maps each Band room to an OpenCode session. Messages from the room
    are forwarded as prompts; SSE events from OpenCode are relayed back as
    room messages, tool-call/result reports, and error events. Platform
    tools come from band-mcp, while `additional_tools` are exposed through
    a separate local MCP server.

    Approval lifecycle (``approval_mode``):
      * ``manual`` -- permission prompts are forwarded to the room; the user
        replies with ``approve``, ``always``, or ``reject`` before a
        configurable timeout (``approval_wait_timeout_s``).
      * ``auto_accept`` -- every permission is approved with ``once``.
      * ``auto_decline`` -- every permission is rejected immediately.

    Exception, in every mode: a permission ask naming one of the adapter's
    OWN registered tools (band platform tools + ``additional_tools``) is
    auto-approved with ``always`` -- platform plumbing must never stall on a
    human approval, matching the codex adapter, which executes band tools
    with no approval gate. Non-tool asks such as OpenCode's ``doom_loop``
    heuristic still follow ``approval_mode``; headless deployments (no human
    in the room) should run ``auto_accept``.

    Question lifecycle (``question_mode``):
      * ``manual`` -- questions are forwarded to the room; the user replies
        with answers or ``reject`` before ``question_wait_timeout_s``.
      * ``auto_reject`` -- questions are rejected immediately.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.EXECUTION, Emit.TASK_EVENTS, Emit.USAGE}
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
            raise BandConfigError(
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
        self._mcp_backend: BandMCPBackend | None = None
        self._rooms: dict[str, _RoomState] = {}
        self._room_by_session: dict[str, str] = {}
        self._state_lock = asyncio.Lock()
        self._system_prompt: str = ""
        # Names of the tools this adapter itself registers with OpenCode
        # (band platform tools + custom tools); populated when the shared MCP
        # backend is built. Permission asks for these are auto-approved.
        self._own_tool_names: frozenset[str] = frozenset()

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

    def _build_turn_system(self, room_id: str, msg: PlatformMessage) -> str:
        """Per-turn system prompt: the static base plus this room's context.

        The band MCP tools' schemas require a ``room_id`` argument (the shared
        backend dispatches tool calls by room), so the model must be told the
        current room id every turn or the platform tools are uncallable —
        the same per-turn room context the ACP client adapter injects.
        """
        requester_name = msg.sender_name or msg.sender_id or "Unknown"
        requester_id = msg.sender_id or "unknown"
        room_context = (
            "## Room Context\n"
            f"Current room_id: {room_id}\n"
            f"Current requester name: {requester_name}\n"
            f"Current requester id: {requester_id}\n"
            "\n"
            "Use each MCP tool's schema for its argument names. When a tool "
            "needs the current room, use the Current room_id value above.\n"
        )
        return f"{self._system_prompt}\n\n{room_context}".strip()

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

        if await room_state.approvals.try_handle_reply(msg.content, msg.sender_id):
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
            session_id, created = await self._ensure_session(room_state, history)
            if Emit.TASK_EVENTS in self.features.emit and (
                room_state.persisted_session_id != session_id or is_session_bootstrap
            ):
                await self._emit_session_task_event(
                    room_state,
                    status="created" if created else "resumed",
                )

            self._begin_turn(room_state, sender_id=msg.sender_id)
            # Snapshot THIS turn's state before the prompt await: prompt_async
            # can span the whole turn (session.idle may arrive mid-POST), and a
            # message racing in during that window would _begin_turn again;
            # reading room_state afterwards would wire this turn's watch task
            # to the wrong turn's future and usage dict.
            release_future = room_state.turn_release_future
            turn_future = room_state.turn_future
            usage_by_message = room_state.usage_by_message
            try:
                await client.prompt_async(
                    session_id,
                    parts=self._build_prompt_parts(
                        msg,
                        participants_msg,
                        contacts_msg,
                        # A newly-created server session holds no prior context, so
                        # seed it with the converted in-session history. This covers
                        # both the 404-recovery case and a fresh session created
                        # because no prior id was recoverable — e.g. turn 2 of an
                        # in-session exchange after the in-memory session id was lost.
                        # Without this the model sees only the latest message and
                        # answers "I don't recall". A reused session (created is
                        # False) already holds the history server-side, so we must
                        # not replay and double it.
                        replay_messages=(history.replay_messages if created else None),
                    ),
                    system=self._build_turn_system(room_id, msg),
                    model=self._build_model_payload(),
                    agent=self.config.agent,
                    variant=self.config.variant,
                )
            except Exception:
                self._clear_turn_state(room_state, expected_future=turn_future)
                raise

            turn_task = asyncio.create_task(
                self._watch_turn_completion(
                    room_state,
                    room_id,
                    turn_future,
                    usage_by_message,
                )
            )
            # Register the watcher only while this turn is still current; a
            # superseded turn's task must not clobber (or be cancelled through)
            # the next turn's ambient pointer.
            if room_state.turn_future is turn_future:
                room_state.turn_task = turn_task

            if release_future is not None:
                await release_future
            if turn_future is not None and turn_future.done():
                await turn_task
        # NOTE: the turn timeout is owned solely by _watch_turn_completion (via
        # asyncio.wait_for), which aborts the session and emits the error event.
        # Nothing awaited here re-raises asyncio.TimeoutError, so on_message has no
        # timeout handler of its own.
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

    def _canonical_tool_name(self, name: str) -> str:
        """Strip OpenCode's ``{server}_`` MCP prefix off one of our own tools.

        OpenCode registers a remote MCP server's tools under
        ``{server}_{tool}`` (verified live: the band server's
        ``band_store_memory`` surfaces as ``band_band_store_memory``). Room
        ``tool_call``/``tool_result`` events must carry the canonical band
        tool name like every other adapter's, so consumers match on one
        vocabulary. Names that aren't ours pass through untouched.
        """
        stripped = name.removeprefix(f"{self.config.mcp_server_name}_")
        return stripped if stripped in self._own_tool_names else name

    def _is_own_band_tool(self, permission: str) -> bool:
        """Whether a permission ask names a tool this adapter registered.

        The ask's ``permission`` field is the flat registered tool name, which
        for an MCP tool carries OpenCode's ``{server}_{tool}`` prefix (see
        ``_canonical_tool_name``); a bare name is accepted too. Non-matches
        are logged at debug so any OpenCode naming drift shows up in live
        logs instead of silently regressing.
        """
        if (
            permission in self._own_tool_names
            or self._canonical_tool_name(permission) in self._own_tool_names
        ):
            return True
        logger.debug(
            "OpenCode permission %r does not name a registered band tool",
            permission,
        )
        return False

    async def _get_or_create_room_state(self, room_id: str) -> _RoomState:
        async with self._state_lock:
            state = self._rooms.get(room_id)
            if state is None:
                state = _RoomState(room_id=room_id)
                state.approvals = RoomApprovals(
                    self.config,
                    ApprovalPorts(
                        room_id=room_id,
                        session_id=lambda: state.session_id,
                        client=lambda: self._client,
                        tools=lambda: state.tools,
                        turn_mentions=lambda: state.pending_mentions,
                        release_turn_wait=lambda: self._release_turn_wait(state),
                        is_own_band_tool=self._is_own_band_tool,
                    ),
                )
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

    async def _ensure_mcp_backend(self) -> BandMCPBackend:
        """Create the shared Band MCP backend (LocalMCPServer with SSE)."""
        if self._mcp_backend is not None:
            return self._mcp_backend

        tool_definitions = list(
            iter_tool_definitions(
                include_memory=Capability.MEMORY in self.features.capabilities,
                include_contacts=Capability.CONTACTS in self.features.capabilities,
            )
        )
        self._own_tool_names = frozenset(
            {definition.name for definition in tool_definitions}
            | {get_custom_tool_name(model) for model, _fn in self._custom_tools}
        )
        backend = await create_band_mcp_backend(
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
            "Shared Band MCP backend started with %d tools (%d custom)",
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
            logger.exception("Failed to start shared Band MCP backend for OpenCode")
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
                async for raw_event in client.iter_events():
                    retry_delay = 1.0  # reset on successful event
                    await self._handle_event(parse_opencode_event(raw_event))
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

    async def _handle_event(self, event: OpencodeEvent) -> None:
        room_state = await self._room_state_for_session(event.session_id)
        if room_state is None:
            return

        match event:
            case MessageUpdatedEvent():
                self._apply_message_update(room_state, event.properties.info)
            case MessagePartUpdatedEvent():
                if event.properties.part is not None:
                    await self._handle_part_update(room_state, event.properties.part)
            case MessagePartDeltaEvent():
                self._apply_part_delta(room_state, event)
            case PermissionAskedEvent():
                await room_state.approvals.on_permission_asked(event.properties)
            case QuestionAskedEvent():
                await room_state.approvals.on_question_asked(event.properties)
            case SessionErrorEvent():
                room_state.last_error_message = describe_error(event.properties.error)
                self._finish_turn(room_state)
            case SessionIdleEvent():
                self._finish_turn(room_state)

    async def _room_state_for_session(
        self, session_id: str | None
    ) -> _RoomState | None:
        if not session_id:
            return None

        async with self._state_lock:
            room_id = self._room_by_session.get(session_id)
            if not room_id:
                return None
            return self._rooms.get(room_id)

    def _apply_message_update(
        self, room_state: _RoomState, info: OpencodeMessageInfo | None
    ) -> None:
        if info is None or info.role != "assistant":
            return
        if info.id:
            room_state.assistant_message_ids.add(info.id)
            if Emit.USAGE in self.features.emit and info.tokens is not None:
                usage = info.tokens.to_turn_usage()
                if not usage.is_empty:
                    room_state.usage_by_message[info.id] = usage
        if info.error is not None and not info.error.is_empty:
            room_state.last_error_message = info.error.describe()

    async def _handle_part_update(
        self, room_state: _RoomState, part: OpencodePart
    ) -> None:
        if not part.id:
            return
        part_id = part.id
        message_id = part.message_id

        if part.type == "text":
            if not message_id or message_id not in room_state.assistant_message_ids:
                return
            room_state.assistant_part_types[part_id] = "text"
            room_state.text_parts[part_id] = part.text or ""
            return

        if part.type == "reasoning":
            if not message_id or message_id not in room_state.assistant_message_ids:
                return
            room_state.assistant_part_types[part_id] = "reasoning"
            return

        if part.type != "tool" or Emit.EXECUTION not in self.features.emit:
            return

        state = part.state
        if state is None:
            return

        tool_name = self._canonical_tool_name(part.tool or "unknown")
        call_id = part.call_id or part_id
        if state.status in {"pending", "running"}:
            if call_id not in room_state.reported_tool_calls:
                room_state.reported_tool_calls.add(call_id)
                await self._report_tool_call(room_state, tool_name, state, call_id)
            return

        if state.status in {"completed", "error"}:
            if call_id not in room_state.reported_tool_calls:
                room_state.reported_tool_calls.add(call_id)
                await self._report_tool_call(room_state, tool_name, state, call_id)
            if call_id not in room_state.reported_tool_results:
                room_state.reported_tool_results.add(call_id)
                await self._report_tool_result(room_state, state, call_id)

    def _apply_part_delta(
        self, room_state: _RoomState, event: MessagePartDeltaEvent
    ) -> None:
        props = event.properties
        if props.field != "text":
            return
        if not props.part_id:
            return
        message_id = props.message_id
        if not message_id or message_id not in room_state.assistant_message_ids:
            return
        if room_state.assistant_part_types.get(props.part_id) != "text":
            return
        room_state.text_parts[props.part_id] = (
            room_state.text_parts.get(props.part_id, "") + props.delta
        )

    async def _ensure_session(
        self, room_state: _RoomState, history: OpencodeSessionState
    ) -> tuple[str, bool]:
        if self._client is None:
            raise RuntimeError("OpenCode client is not initialized")

        restored_session_id = room_state.session_id or history.session_id
        created = False

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

        return session_id, created

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
        # A fresh dict, not .clear(): the previous turn's watch task drains the
        # dict instance it captured, so a new turn must not empty it out from
        # under a still-pending _emit_turn_usage (same snapshot idea as passing
        # turn_future into _watch_turn_completion).
        room_state.usage_by_message = {}
        room_state.last_error_message = None

    async def _watch_turn_completion(
        self,
        room_state: _RoomState,
        room_id: str,
        turn_future: asyncio.Future[None] | None,
        usage_by_message: dict[str, TurnUsage],
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
            # Tokens spent before the timeout were still spent — emit them, same
            # as the success path (best-effort; no-op if none captured).
            await self._emit_turn_usage(room_state, usage_by_message)
        else:
            await self._deliver_fallback_text(room_state)
            await self._emit_turn_usage(room_state, usage_by_message)
        finally:
            # Release the on_message waiter even if delivering the reply or
            # emitting usage raised (e.g. a sender-less turn has no one to
            # @mention, which the platform rejects) — otherwise on_message
            # waits on the captured release_future forever.
            self._release_turn_wait(room_state)
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

        room_state.approvals.cancel()
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

    async def _emit_turn_usage(
        self,
        room_state: _RoomState,
        usage_by_message: dict[str, TurnUsage],
    ) -> None:
        """Sum the turn's per-assistant-message usage and emit it.

        Takes the turn-owned dict captured by the watch task (not
        ``room_state.usage_by_message``, which a new turn may have replaced by
        the time this runs). A no-op when usage reporting is off
        (``Emit.USAGE`` absent) or nothing was captured: the base
        ``emit_usage`` skips an empty total. A live OpenCode server reports
        ``tokens`` on each assistant ``info``; mocked/offline runs don't, so
        the total is simply empty there.
        """
        if room_state.tools is None:
            return
        total = sum(usage_by_message.values(), TurnUsage())
        await self.emit_usage(room_state.tools, total)

    async def _report_tool_call(
        self,
        room_state: _RoomState,
        tool_name: str,
        state: OpencodeToolState,
        call_id: str,
    ) -> None:
        if room_state.tools is None:
            return
        try:
            await room_state.tools.send_event(
                json.dumps(
                    {
                        "name": tool_name,
                        "args": state.input,
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
        state: OpencodeToolState,
        call_id: str,
    ) -> None:
        if room_state.tools is None:
            return
        output: Any
        if state.status == "error":
            output = {"error": state.error or "OpenCode tool failed"}
        else:
            output = state.reported_output

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

    def _format_http_error(self, exc: httpx.HTTPStatusError) -> str:
        try:
            payload = exc.response.json()
        except ValueError:
            payload = exc.response.text
        return f"OpenCode request failed ({exc.response.status_code}): {payload}"
