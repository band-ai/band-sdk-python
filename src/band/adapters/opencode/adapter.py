"""OpenCode server adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Any

import httpx
from typing_extensions import Unpack

from band.adapters.opencode.approvals import ApprovalPorts, RoomApprovals
from band.adapters.opencode.config import OpencodeAdapterConfig
from band.converters.opencode import OpencodeHistoryConverter
from band.core.exceptions import BandConnectionError
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    ToolEventKey,
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
    OpencodeToolStatus,
    PermissionAskedEvent,
    QuestionAskedEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    describe_error,
    parse_opencode_event,
)
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name
from band.runtime.prompts import render_system_prompt
from band.runtime.tools import (
    CHAT_ID_FIELD_NAME,
    is_room_posting_tool,
    iter_tool_definitions,
)

logger = logging.getLogger(__name__)

_OPENCODE_SYSTEM_NOTE = """\
Responses are relayed back into the Band room by the adapter.
Use the band_ prefixed tools (e.g. band_send_message) for Band platform actions when available.
When you need approval or clarification, ask clearly and wait for the user's next room message.
"""

_MCP_SERVER_ID_LENGTH = 8


@dataclass
class RoomState:
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
    # Set when a room-posting band tool (band_send_message) completed this turn,
    # so the text fallback stays silent instead of double-posting the reply.
    replied_via_room_tool: bool = False
    # Bound in _get_or_create_room_state, immediately after construction.
    approvals: RoomApprovals = field(init=False)
    last_error_message: str | None = None
    persisted_session_id: str | None = None
    # Per-assistant-message usage for the current turn (last-write-wins per id,
    # since message.updated streams repeatedly). Summed across messages at turn
    # end — a tool loop produces several assistant messages.
    usage_by_message: dict[str, TurnUsage] = field(default_factory=dict)

    def begin_turn(self, sender_id: str | None) -> None:
        """Reset reply state and create the futures for one new turn."""
        loop = asyncio.get_running_loop()
        self.turn_future = loop.create_future()
        self.turn_release_future = loop.create_future()
        self.turn_task = None
        self.pending_mentions = [{"id": sender_id}] if sender_id else []
        self.text_parts.clear()
        self.assistant_message_ids.clear()
        self.assistant_part_types.clear()
        self.reported_tool_calls.clear()
        self.reported_tool_results.clear()
        self.replied_via_room_tool = False
        # A new dict preserves the prior turn's snapshot for its watch task.
        self.usage_by_message = {}
        self.last_error_message = None

    def record_message(
        self, info: OpencodeMessageInfo | None, *, emit_usage: bool
    ) -> None:
        """Record the assistant message metadata relevant to the current turn."""
        if info is None or info.role != "assistant":
            return
        if info.id:
            self.assistant_message_ids.add(info.id)
            if emit_usage and info.tokens is not None:
                usage = info.tokens.to_turn_usage()
                if not usage.is_empty:
                    self.usage_by_message[info.id] = usage
        if info.error is not None and not info.error.is_empty:
            self.last_error_message = info.error.describe()

    def track_assistant_part(self, part: OpencodePart) -> None:
        """Remember text and reasoning parts belonging to the assistant reply."""
        if not part.id or part.message_id not in self.assistant_message_ids:
            return
        self.assistant_part_types[part.id] = part.type
        if part.type == "text":
            self.text_parts[part.id] = part.text or ""

    def append_text_delta(self, event: MessagePartDeltaEvent) -> None:
        """Append a text delta only after its assistant text part is known."""
        props = event.properties
        if (
            props.field != "text"
            or not props.part_id
            or props.message_id not in self.assistant_message_ids
            or self.assistant_part_types.get(props.part_id) != "text"
        ):
            return
        self.text_parts[props.part_id] = (
            self.text_parts.get(props.part_id, "") + props.delta
        )

    def mark_tool_call(self, call_id: str) -> bool:
        """Return whether this is the first report for a tool call."""
        if call_id in self.reported_tool_calls:
            return False
        self.reported_tool_calls.add(call_id)
        return True

    def mark_tool_result(self, call_id: str) -> bool:
        """Return whether this is the first report for a tool result."""
        if call_id in self.reported_tool_results:
            return False
        self.reported_tool_results.add(call_id)
        return True


class OpencodeAdapter(SimpleAdapter[OpencodeSessionState]):
    """Band adapter for the OpenCode HTTP server.

    Maps each Band room to an OpenCode session. Messages from the room
    are forwarded as prompts; SSE events from OpenCode are relayed back as
    room messages, tool-call/result reports, and error events. Band platform
    tools and `additional_tools` are served together by one in-process MCP
    server, registered with OpenCode over SSE.

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

    ``Emit.TASK_EVENTS`` is more than narration here: the room's OpenCode
    ``session_id`` is persisted in task event metadata and read back by
    OpencodeHistoryConverter to resume the server-side session. Narrowing
    ``emit`` to exclude it doesn't just silence updates, it also stops
    resumption -- every restart creates a fresh OpenCode session instead of
    reattaching. Leave it in ``emit`` unless that's intended.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.TASK_EVENTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: OpencodeAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        history_converter: OpencodeHistoryConverter | None = None,
        client_factory: Callable[[OpencodeAdapterConfig], OpencodeClientProtocol]
        | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        self._config = config or OpencodeAdapterConfig()

        super().__init__(
            history_converter=history_converter or OpencodeHistoryConverter(),
            **features,
        )
        self.config = self._config
        # Set in ``on_started`` from the agent identity. OpenCode keys MCP
        # registrations globally by name, so this must stay stable across an
        # agent restart (to refresh the same registration) yet differ for
        # concurrent agents sharing one serve.
        self._mcp_server_name = self._config.mcp_server_name
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        # Startup reachability check only makes sense against a real server;
        # an injected factory fakes that boundary (tests, custom transports).
        self._preflight_enabled = client_factory is None
        self._client_factory = client_factory or self._default_client_factory
        self._client: OpencodeClientProtocol | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._mcp_backend: BandMCPBackend | None = None
        self._rooms: dict[str, RoomState] = {}
        self._room_by_session: dict[str, str] = {}
        self._state_lock = asyncio.Lock()
        self._system_prompt: str = ""
        # The tools this adapter registers with OpenCode (band platform tools +
        # custom tools). Computed once at construction -- both inputs are known
        # here -- and reused when the shared MCP backend is built. Deriving the
        # names eagerly keeps the "is this our own band tool?" auto-approve
        # check (and room-posting detection) independent of MCP-registration
        # timing, so a second room's first turn can't race an empty set.
        self._refresh_tool_definitions()

    def _refresh_tool_definitions(self) -> None:
        self._tool_definitions = list(
            iter_tool_definitions(capabilities=self.features.capabilities)
        )
        self._own_tool_names = frozenset(
            {definition.name for definition in self._tool_definitions}
            | {get_custom_tool_name(model) for model, _fn in self._custom_tools}
        )

    def apply_effective_features(self, features: AdapterFeatures) -> None:
        """Keep the MCP registration aligned with negotiated capabilities."""
        super().apply_effective_features(features)
        self._refresh_tool_definitions()

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)

        self._mcp_server_name = self._agent_mcp_server_name(agent_name)

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

        await self._preflight_server()
        self._log_startup_config(agent_name)

    async def _preflight_server(self) -> None:
        """Fail fast at startup when no OpenCode server answers at base_url.

        The working client stays lazy (built on the first turn), so the probe
        uses a transient one. Without this, an unreachable server surfaces as
        a per-turn error event in every room instead of one clear startup
        failure naming the fix.
        """
        if not self._preflight_enabled:
            return
        probe = self._client_factory(self.config)
        try:
            await probe.health()
        except httpx.HTTPError as exc:
            raise BandConnectionError(
                f"OpenCode server not reachable at {self.config.base_url}: {exc}. "
                "Start one with `opencode serve --hostname 127.0.0.1 --port 4096` "
                "or point OpencodeAdapterConfig.base_url at a running server."
            ) from exc
        finally:
            await probe.close()

    def _agent_mcp_server_name(self, agent_identity: str) -> str:
        """Return a stable, serve-global MCP name for one Band identity."""
        digest = hashlib.sha256(agent_identity.encode()).hexdigest()[
            :_MCP_SERVER_ID_LENGTH
        ]
        return f"{self.config.mcp_server_name}_{digest}"

    def _mcp_tool_visibility(self) -> dict[str, bool]:
        """Expose this agent's MCP tools while hiding sibling registrations."""
        namespace = f"{self.config.mcp_server_name}_*"
        current_registration = f"{self._mcp_server_name}_*"
        # OpenCode applies the last matching rule, so the narrow allow follows
        # the namespace-wide deny.
        return {namespace: False, current_registration: True}

    def _build_turn_system(self, room_id: str, msg: PlatformMessage) -> str:
        """Per-turn system prompt: the static base plus this room's context.

        The band MCP tools' schemas require a ``chat_id`` argument (the shared
        backend dispatches tool calls by room), so the model must be told the
        current room id every turn or the platform tools are uncallable —
        the same per-turn room context the ACP client adapter injects.
        """
        requester_name = msg.sender_name or msg.sender_id or "Unknown"
        requester_id = msg.sender_id or "unknown"
        room_context = (
            "## Room Context\n"
            f"Current {CHAT_ID_FIELD_NAME}: {room_id}\n"
            f"Current requester name: {requester_name}\n"
            f"Current requester id: {requester_id}\n"
            "\n"
            "Use each MCP tool's schema for its argument names. When a tool "
            f"needs the current room, use the Current {CHAT_ID_FIELD_NAME} "
            "value above.\n"
        )
        return f"{self._system_prompt}\n\n{room_context}".strip()

    def _log_startup_config(self, agent_name: str) -> None:
        logger.info(
            "OpenCode adapter started: agent=%s, base_url=%s, "
            "provider=%s, model=%s, approval_mode=%s, "
            "question_mode=%s, execution_reporting=%s, "
            "task_events=%s, custom_tools=%d",
            agent_name,
            self.config.base_url,
            self.config.provider_id or "default",
            self.config.model_id or "default",
            self.config.approval_mode,
            self.config.question_mode,
            Emit.TOOL_CALLS in self.features.emit,
            Emit.TASK_EVENTS in self.features.emit,
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

        if self._client is None:
            agent_id = getattr(tools, "agent_id", None)
            self._mcp_server_name = self._agent_mcp_server_name(
                agent_id or self.agent_name
            )

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

        turn_future: asyncio.Future[None] | None = None
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
                # Turn-phase diagnostics (classify a stuck turn from CI logs):
                # if 'returned' never follows 'start', prompt_async is blocking
                # (submission/scheduling), which no watcher bounds -- see the
                # note below on _watch_turn_completion owning the timeout.
                logger.info(
                    "OpenCode turn: prompt_async start room=%s session=%s",
                    room_id,
                    session_id,
                )
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
                    tools=self._mcp_tool_visibility(),
                )
                logger.info(
                    "OpenCode turn: prompt_async returned room=%s session=%s",
                    room_id,
                    session_id,
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
        except asyncio.CancelledError:
            # The runtime interrupts a turn by cancelling this coroutine, but the
            # watcher runs detached. Left alone it outlives the interrupt: it
            # posts the very reply the user stopped, and holds the room's busy
            # guard until turn_timeout_s. Drop the turn state first (that cancels
            # the watcher), then ask OpenCode to stop working.
            if turn_future is not None:
                self._clear_turn_state(room_state, expected_future=turn_future)
                await self._abort_session(room_state, "interrupted")
            raise
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
        room_state: RoomState | None = None
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
        stripped = name.removeprefix(f"{self._mcp_server_name}_")
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

    async def _get_or_create_room_state(self, room_id: str) -> RoomState:
        async with self._state_lock:
            state = self._rooms.get(room_id)
            if state is None:
                state = RoomState(room_id=room_id)
                state.approvals = RoomApprovals(
                    self.config,
                    ApprovalPorts(
                        room_id=room_id,
                        session_id=lambda: state.session_id,
                        client=lambda: self._client,
                        tools=lambda: state.tools,
                        turn_mentions=lambda: state.pending_mentions,
                        release_turn_wait=lambda: self._release_turn_wait(state),
                        fail_turn=lambda message: self._fail_turn(state, message),
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
                # Registration is part of client startup. Keep concurrent room
                # starts behind the same barrier so no first turn can run before
                # this client's Band tools are visible to OpenCode.
                await self._register_mcp_backend()

    async def _ensure_mcp_backend(self) -> BandMCPBackend:
        """Create the shared Band MCP backend (LocalMCPServer with SSE)."""
        if self._mcp_backend is not None:
            return self._mcp_backend

        backend = await create_band_mcp_backend(
            kind="sse",
            tool_definitions=self._tool_definitions,
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
                name=self._mcp_server_name,
                url=local_server.sse_url,
            )
            logger.info(
                "Registered MCP server %s at %s with OpenCode",
                self._mcp_server_name,
                local_server.sse_url,
            )
        except Exception:
            logger.exception(
                "Failed to register MCP server %s with OpenCode",
                self._mcp_server_name,
            )

    async def _shutdown_client(self) -> None:
        async with self._state_lock:
            # ``on_cleanup`` decides to shut down after removing the last room,
            # then releases the lock before stopping network resources. A new
            # room may arrive in that gap; keep the shared client registered for
            # it and let that room's eventual cleanup own shutdown instead.
            if self._rooms:
                return
            event_task = self._event_task
            client = self._client
            mcp_backend = self._mcp_backend
            self._event_task = None
            self._client = None
            self._mcp_backend = None

            # OpenCode keys MCP registrations globally by name, so the
            # disconnect stays under the lock that also guards registration.
            # Released early, it could land after a successor room registered
            # the same name and strip tools that nothing would re-register.
            if mcp_backend is not None and client is not None:
                try:
                    await client.disconnect_mcp_server(self._mcp_server_name)
                except Exception:
                    logger.debug(
                        "Failed to disconnect MCP server %s (OpenCode may already be stopped)",
                        self._mcp_server_name,
                    )

        # What follows acts only on objects already detached from ``self``, so
        # no successor can be affected — and the lock must not be held across a
        # stop that waits out OpenCode's open SSE read.
        if mcp_backend is not None:
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
                logger.info(
                    "OpenCode turn: session.idle room=%s session=%s",
                    room_state.room_id,
                    event.session_id,
                )
                self._finish_turn(room_state)

    async def _room_state_for_session(self, session_id: str | None) -> RoomState | None:
        if not session_id:
            return None

        async with self._state_lock:
            room_id = self._room_by_session.get(session_id)
            if not room_id:
                return None
            return self._rooms.get(room_id)

    def _apply_message_update(
        self, room_state: RoomState, info: OpencodeMessageInfo | None
    ) -> None:
        room_state.record_message(info, emit_usage=Emit.USAGE in self.features.emit)

    async def _handle_part_update(
        self, room_state: RoomState, part: OpencodePart
    ) -> None:
        if not part.id:
            return

        match part.type:
            case "text" | "reasoning":
                room_state.track_assistant_part(part)
            case "tool":
                await self._report_tool_part(room_state, part)

    async def _report_tool_part(
        self, room_state: RoomState, part: OpencodePart
    ) -> None:
        """Note a room-posting reply and report the tool's call/result.

        Room-posting detection runs regardless of ``Emit.TOOL_CALLS`` (which only
        governs the tool_call/tool_result narration); the text-fallback
        suppression must hold even when execution reporting is off.
        """
        if part.state is None:
            return

        state = part.state
        tool_name = self._canonical_tool_name(part.tool or "unknown")

        # A completed room-posting band tool IS the turn's reply -- suppress the
        # text fallback (codex/copilot_sdk/ACP parity). An errored call did not
        # post, so it must not suppress. ``status`` is the raw wire string, so
        # compare by value (the StrEnum member equals its string).
        if state.status == OpencodeToolStatus.COMPLETED and is_room_posting_tool(
            tool_name
        ):
            room_state.replied_via_room_tool = True

        if Emit.TOOL_CALLS not in self.features.emit:
            return

        match state.status:
            case (
                OpencodeToolStatus.PENDING
                | OpencodeToolStatus.RUNNING
                | OpencodeToolStatus.COMPLETED
                | OpencodeToolStatus.ERROR
            ):
                pass
            case _:
                return

        assert part.id is not None
        call_id = part.call_id or part.id

        if room_state.mark_tool_call(call_id):
            await self._report_tool_call(room_state, tool_name, state, call_id)

        match state.status:
            case OpencodeToolStatus.COMPLETED | OpencodeToolStatus.ERROR:
                if room_state.mark_tool_result(call_id):
                    await self._report_tool_result(room_state, state, call_id)

    def _apply_part_delta(
        self, room_state: RoomState, event: MessagePartDeltaEvent
    ) -> None:
        room_state.append_text_delta(event)

    async def _ensure_session(
        self, room_state: RoomState, history: OpencodeSessionState
    ) -> tuple[str, bool]:
        if self._client is None:
            raise RuntimeError("OpenCode client is not initialized")

        restored_session_id = room_state.session_id
        if restored_session_id is None and history.session_id:
            # A session persisted before the adapter recorded its registration
            # carries no name. Treating that as ours keeps the upgrade from
            # discarding every existing room's server-side conversation.
            if history.mcp_server_name in (None, self._mcp_server_name):
                restored_session_id = history.session_id
            else:
                logger.info(
                    "OpenCode session %s belongs to MCP registration %s; "
                    "creating a new session for %s",
                    history.session_id,
                    history.mcp_server_name,
                    self._mcp_server_name,
                )
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

    def _begin_turn(self, room_state: RoomState, *, sender_id: str | None) -> None:
        room_state.begin_turn(sender_id)

    async def _watch_turn_completion(
        self,
        room_state: RoomState,
        room_id: str,
        turn_future: asyncio.Future[None] | None,
        usage_by_message: dict[str, TurnUsage],
    ) -> None:
        if turn_future is None:
            return

        # 'watcher started' after 'prompt_async returned' but no later
        # 'session.idle' points at a lost/late SSE terminal event, not a slow
        # model -- distinct from the 'timed out' branch below firing at 300s.
        logger.info(
            "OpenCode turn: watcher started room=%s session=%s timeout=%ss",
            room_id,
            room_state.session_id,
            self.config.turn_timeout_s,
        )
        try:
            await self._await_turn(room_state, turn_future)
        except asyncio.TimeoutError:
            logger.warning(
                "OpenCode turn timed out for room %s (session=%s)",
                room_id,
                room_state.session_id,
            )
            await self._abort_session(room_state, "timed-out")
            if room_state.tools:
                await room_state.tools.send_event(
                    "OpenCode timed out before completing the turn.",
                    "error",
                )
            # Tokens spent before the timeout were still spent — emit them, same
            # as the success path (best-effort; no-op if none captured).
            await self._emit_turn_usage(room_state, usage_by_message)
        else:
            try:
                await self._deliver_fallback_text(room_state)
                await self._emit_turn_usage(room_state, usage_by_message)
            except Exception:
                logger.exception(
                    "Failed to deliver the OpenCode turn result for room %s", room_id
                )
                await self._report_delivery_failure(room_state)
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

    async def _abort_session(self, room_state: RoomState, reason: str) -> None:
        """Best-effort: tell OpenCode to stop working on this room's session."""
        if not (self._client and room_state.session_id):
            return
        try:
            await self._client.abort_session(room_state.session_id)
        except Exception:
            logger.exception(
                "Failed to abort %s OpenCode session %s",
                reason,
                room_state.session_id,
            )

    async def _report_delivery_failure(self, room_state: RoomState) -> None:
        """Tell the room the turn finished but its result could not be posted.

        An event needs no mentions, so it still lands when the reply itself was
        rejected for having none.
        """
        if room_state.tools is None:
            return
        try:
            await room_state.tools.send_event(
                "OpenCode finished the turn but the result could not be posted "
                "to the room.",
                "error",
            )
        except Exception:
            logger.exception(
                "Failed to report the OpenCode delivery failure for room %s",
                room_state.room_id,
            )

    async def _await_turn(
        self, room_state: RoomState, turn_future: asyncio.Future[None]
    ) -> None:
        """Await turn completion, but don't charge human-approval time to the
        compute budget.

        A manual permission/question parks the turn on a human reply, which is
        bounded by the ask's own expiry timer -- not by ``turn_timeout_s``. So
        the deadline is ``turn_timeout_s`` of *compute*: it moves out by however
        long the turn sat on a human, which is why the budget is recomputed each
        slice rather than fixed when the watcher started -- an approval that
        arrives before the deadline still has to leave the resumed work its full
        budget. ``shield`` keeps a timed-out slice from cancelling the
        still-running turn.
        """
        loop = asyncio.get_running_loop()
        approvals = room_state.approvals
        started = loop.time()

        def deadline() -> float:
            return started + self.config.turn_timeout_s + approvals.human_wait_seconds

        while True:
            try:
                await asyncio.wait_for(
                    asyncio.shield(turn_future), max(deadline() - loop.time(), 0.0)
                )
                return
            except asyncio.TimeoutError:
                # Genuinely out of compute budget with nobody deliberating.
                if not approvals.awaiting_human() and deadline() <= loop.time():
                    raise
                # Otherwise wait out any parked ask (a no-op if none) and retry
                # against the extended deadline.
                await approvals.wait_until_idle()

    def _release_turn_wait(self, room_state: RoomState) -> None:
        self._resolve_future(room_state.turn_release_future)

    def _finish_turn(self, room_state: RoomState) -> None:
        self._resolve_future(room_state.turn_future)
        self._resolve_future(room_state.turn_release_future)

    def _fail_turn(self, room_state: RoomState, message: str) -> None:
        room_state.last_error_message = message
        self._finish_turn(room_state)

    def _clear_turn_state(
        self,
        room_state: RoomState,
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
        self, room_state: RoomState, *, status: str
    ) -> None:
        if room_state.tools is None or not room_state.session_id:
            return

        created_at = datetime.now(timezone.utc).isoformat()
        # Best-effort bookkeeping: a transient post failure must not abort the
        # turn before the model runs (the outer on_message handler would catch
        # it and drop the user's message). Leave persisted_session_id unset on
        # failure so the next turn retries the event.
        try:
            await room_state.tools.send_event(
                f"OpenCode session {status}: `{room_state.session_id}`",
                "task",
                metadata={
                    "opencode_session_id": room_state.session_id,
                    "opencode_mcp_server_name": self._mcp_server_name,
                    "opencode_room_id": room_state.room_id,
                    "opencode_created_at": created_at,
                },
            )
        except Exception:
            logger.exception(
                "Failed to emit OpenCode session task event for room %s",
                room_state.room_id,
            )
            return
        room_state.persisted_session_id = room_state.session_id

    async def _deliver_fallback_text(self, room_state: RoomState) -> None:
        if room_state.tools is None or not self.config.fallback_send_agent_text:
            return

        text = "\n".join(
            part_text.strip()
            for part_text in room_state.text_parts.values()
            if part_text.strip()
        ).strip()

        # If this logs but the room never sees a reply, the fallback REST post
        # (or the test's observer WebSocket) is the fault, not model completion.
        logger.info(
            "OpenCode turn: delivering fallback room=%s "
            "(text=%d chars, error=%s, replied_via_tool=%s)",
            room_state.room_id,
            len(text),
            bool(room_state.last_error_message),
            room_state.replied_via_room_tool,
        )

        # A room-posting band tool already delivered the reply; don't double-post
        # its plain text or a "no reply" filler. An error is still surfaced --
        # it is not a text reply.
        replied = room_state.replied_via_room_tool
        try:
            if text and not replied:
                await room_state.tools.send_message(
                    text, mentions=room_state.pending_mentions
                )
            elif room_state.last_error_message:
                await room_state.tools.send_event(
                    room_state.last_error_message, "error"
                )
            elif not replied:
                await room_state.tools.send_message(
                    "OpenCode completed the turn without a text reply.",
                    mentions=room_state.pending_mentions,
                )
        finally:
            room_state.pending_mentions = []

    async def _emit_turn_usage(
        self,
        room_state: RoomState,
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
        room_state: RoomState,
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
                        ToolEventKey.NAME: tool_name,
                        ToolEventKey.ARGS: state.input,
                        ToolEventKey.TOOL_CALL_ID: call_id,
                    }
                ),
                "tool_call",
            )
        except Exception:
            logger.exception("Failed to report OpenCode tool_call for %s", call_id)

    async def _report_tool_result(
        self,
        room_state: RoomState,
        state: OpencodeToolState,
        call_id: str,
    ) -> None:
        if room_state.tools is None:
            return
        output: Any
        if state.status == OpencodeToolStatus.ERROR:
            output = {"error": state.error or "OpenCode tool failed"}
        else:
            output = state.reported_output

        try:
            await room_state.tools.send_event(
                json.dumps(
                    {
                        ToolEventKey.OUTPUT: output,
                        ToolEventKey.TOOL_CALL_ID: call_id,
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
