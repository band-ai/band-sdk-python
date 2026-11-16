"""Codex app-server adapter."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar

from band_sdk_core import AgentFailure
from typing_extensions import Unpack

from band.adapters.codex.approvals import (
    APPROVAL_OPTIONS,
    ApprovalDecision,
    CodexApprovals,
    PendingApproval,
    approval_response,
    approval_summary,
    approval_type,
    native_approval_key,
)
from band.adapters.codex.commands import (
    APPROVAL_COMMAND_DECISIONS,
    APPROVAL_COMMANDS,
    APPROVAL_REQUESTED_TEMPLATE,
    APPROVAL_RESOLVED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    HELP_TEXT,
    MODEL_LIST_WORDS,
    NO_APPROVALS_TO_RESOLVE_MESSAGE,
    PROCESS_COMMANDS,
    CodexCommand,
    CodexSubcommand,
    parse_command,
)
from band.adapters.codex.config import CodexAdapterConfig
from band.adapters.codex.dynamictools import (
    UNOWNED_TOOL_CALL_TEXT,
    DynamicToolCall,
    answer_tool_call,
    dynamic_tools,
    text_tool_result,
    tools_by_name,
)
from band.adapters.codex.items import (
    ACTIVITY_ITEM_TYPES,
    TOOL_ITEM_TYPES,
    item_call_id,
    tool_item,
)
from band.adapters.codex.models import supported_efforts, visible_model_ids
from band.adapters.codex.narration import (
    RAW_TASK_METHODS,
    TaskTitles,
    TurnRef,
    approval_resolved,
    diff_updated,
    error_notification,
    item_completed,
    plan_steps,
    raw_task_event,
    task_event_content,
    token_usage,
    turn_error_message,
    turn_failed,
    turn_usage,
)
from band.adapters.codex.roomstate import RoomCodexClient, ThreadSettings
from band.adapters.codex.rpc import (
    CodexClientProtocol,
    event_params,
    event_thread_id,
    event_turn_id,
    loss_reason,
    refuse_unhandled_request,
    result_entry_id,
)
from band.adapters.codex.sandbox import (
    ENFORCED_SANDBOX_MODES,
    SANDBOX_CONFIRM_FLAG,
    SANDBOX_MODE_CHOICES,
    WINDOWS_SANDBOX_MISSING_WARNING,
    WINDOWS_SANDBOX_SETUP_WARNING,
    CodexSandboxMode,
    apply_thread_sandbox,
    apply_turn_sandbox,
    canonical_sandbox_key,
    normalize_sandbox_mode,
)
from band.adapters.codex.selfconfig import (
    SILENT_REPORTING_TOOLS,
    self_config_tools,
)
from band.adapters.codex.turnstate import (
    PROCESS_NOT_READY_ERROR,
    TRANSPORT_LOST_ERROR,
    TURN_PROCESSING_ERROR,
    CodexContinuityError,
    CodexTurn,
    NativeMessage,
    NativeTurn,
    TurnResult,
    audience_sender_ids,
    undelivered_events,
)
from band.converters.codex import CODEX_RESUME_RECORD, CodexHistoryConverter
from band.converters.helpers import (
    HISTORY_REPLAY_HEADER,
    build_replay_messages,
    new_message_marker,
)
from band.core.content import has_visible_content
from band.core.defaultmodels import OPENAI_MODEL
from band.core.delivery import (
    DeliveryFailedError,
    automatic_mentions,
    deliver_notice,
    finish_automatic_reply,
    reraise_delivery_cause,
)
from band.core.exceptions import (
    HOST_CONTROL_ERRORS,
    CleanupIncompleteError,
    InputNotAccepted,
    ProviderRunError,
    TerminalProviderFailure,
)
from band.core.protocols import (
    FAILURE_CODE_TIMEOUT,
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    AgentToolsProtocol,
    TurnResultAlreadyReported,
    send_event_safe,
)
from band.core.retention import ReleaseCause, ReleaseOutcome, RoomRetention
from band.core.retention.cleanup import spawn_owned
from band.core.simple_adapter import BatchInputAdapter
from band.core.turn import detached_turn
from band.core.turn_lifecycle import (
    ApprovalInterruptMixin,
    BackendTurns,
    TaskRole,
    TurnTask,
    bind_turn_task,
    drain_turn_tasks,
    resource_tools,
    start_releasable_turn,
)
from band.core.types import (
    Capability,
    Emit,
    FeatureKwargs,
    PlatformMessage,
    distinct_sender_ids,
)
from band.integrations.codex import (
    TRANSPORT_CLOSED,
    CodexJsonRpcError,
    CodexStdioClient,
    RpcEvent,
)
from band.integrations.codex.activity import CommandResource
from band.integrations.codex.types import (
    CODEX_APPROVAL_METHODS,
    CODEX_PROVIDER,
    CODEX_TRANSPORT_CLOSED_FAILURE_CODE,
    DYNAMIC_TOOL_CALL_METHOD,
    WINDOWS_PLATFORM_OS,
    CodexItemType,
    CodexMetadataKey,
    CodexNotificationMethod,
    CodexRequestMethod,
    CodexSessionState,
    CodexTokenUsage,
    CodexTurnStatus,
    CodexWindowsSandboxReadiness,
)
from band.runtime.activity import permission_wait
from band.runtime.custom_tools import (
    CustomToolDef,
)
from band.runtime.decisions import (
    APPROVAL_NOT_PENDING_TEMPLATE,
    Timeout,
)
from band.runtime.formatters import (
    build_participants_message,
    format_current_inputs,
)
from band.runtime.prompts import COMMUNICATION_INSTRUCTIONS, render_system_prompt
from band.runtime.sessionresume import SessionSeed, fetch_transcript
from band.runtime.toolactivity import (
    end_activity,
    start_memory_activity,
    start_native_tool_activity,
)
from band.workspaces import RoomWorkspaces

logger = logging.getLogger(__name__)

SYSTEM_CONTEXT_PREFIX = "[System]:"


class CodexAdapter(ApprovalInterruptMixin, BatchInputAdapter[CodexSessionState]):
    """
    Codex adapter backed by codex app-server (stdio or websocket transport).

    One Band room maps to one Codex thread. A fresh thread is sent the room's
    transcript and recorded once, with ``CODEX_RESUME_RECORD`` whatever the
    ``emit`` setting, after that seeding turn completes. A thread is seeded
    only when its first turn completed; an interrupted or cancelled seed is
    abandoned like a failed one. A restart resumes the newest recorded thread.
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset(
        {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.TASK_EVENTS, Emit.USAGE}
    )
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.MEMORY, Capability.CONTACTS, Capability.TASKS, Capability.FILES}
    )

    def __init__(
        self,
        config: CodexAdapterConfig | None = None,
        *,
        additional_tools: list[CustomToolDef] | None = None,
        history_converter: CodexHistoryConverter | None = None,
        client_factory: Callable[[CodexAdapterConfig], CodexClientProtocol]
        | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        self._config = config or CodexAdapterConfig()

        super().__init__(
            history_converter=history_converter or CodexHistoryConverter(), **features
        )
        self.config = self._config
        self._custom_tools: list[CustomToolDef] = list(additional_tools or [])
        if self.config.enable_self_config_tools:
            self._custom_tools.extend(
                self_config_tools(self._require_active_client_state, self.config)
            )
        if self.config.cwd is not None:
            raise ValueError(
                "cwd is not supported; use workspace_for_room or the default"
            )
        if self.config.transport != "stdio":
            raise ValueError(
                "only stdio Codex transport guarantees room process isolation"
            )
        if client_factory is not None:
            raise ValueError(
                "custom Codex clients cannot guarantee room process isolation"
            )
        self._room_clients: dict[str, RoomCodexClient] = {}
        # Final disposal gives a room's turn ``turn_settle_timeout_s`` to wind
        # down before cancelling it.
        self._retention = RoomRetention(
            self.config.retention, settle_grace_s=self.config.turn_settle_timeout_s
        )
        self._workspaces = RoomWorkspaces(self.config.workspace_for_room)
        self._active_room: ContextVar[str | None] = ContextVar(
            "codex_active_room", default=None
        )
        self._system_prompt: str = ""
        self._room_threads: dict[str, str] = {}
        self._prompt_injected_rooms: set[str] = set()
        self._task_titles = TaskTitles()
        self._approvals = CodexApprovals(
            max_pending=self.config.max_pending_approvals_per_room,
            max_session_approved=self.config.max_session_approved_per_room,
            max_audit=self.config.max_approval_audit_per_room,
            granularity=self.config.session_approval_granularity,
        )
        # A detached turn releases the input handler during manual approval
        # so the room can dispatch the reply that resolves it.
        self._turn_release: dict[str, asyncio.Future[None]] = {}
        self._turn_tasks: dict[str, TurnTask] = {}
        self._backend_turns = BackendTurns[CodexTurn](
            self._interrupt_codex_turn, provider=CODEX_PROVIDER
        )
        # Token usage tracking per thread
        self._token_usage: dict[str, CodexTokenUsage] = {}
        # Per-room sandbox overrides (set via /sandbox command)
        self._sandbox_overrides: dict[str, str] = {}
        # Every room's app-server shares one config, so its readiness is checked once.
        self._windows_sandbox_checked = False

    def _room_closing(self, room_id: str) -> bool:
        """Whether the room admits no more work; a forgotten room is closed."""
        room = self._room_clients.get(room_id)
        return room is None or room.retained.closing

    @property
    def backend_turns(self) -> BackendTurns[CodexTurn]:
        return self._backend_turns

    def _room_client(self, room_id: str) -> RoomCodexClient:
        room = self._room_clients.get(room_id)
        if room is not None:
            return room
        self._retention.require_admission()
        with self._workspaces.claiming(room_id) as workspace:
            room = RoomCodexClient(room_id=room_id, workspace=workspace)
            room.retained = self._retention.register(
                room_id,
                acquire=partial(self._acquire, room),
                release=partial(self._release, room),
                dispose=partial(self._dispose, room),
            )
        self._room_clients[room_id] = room
        return room

    def _active_client_state(self) -> RoomCodexClient | None:
        room_id = self._active_room.get()
        return self._room_clients.get(room_id) if room_id is not None else None

    def _require_active_client_state(self) -> RoomCodexClient:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex operation requires a room context")
        return state

    @property
    def _selected_model(self) -> str | None:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex client requires a room context")
        return state.selected_model

    @_selected_model.setter
    def _selected_model(self, value: str | None) -> None:
        state = self._active_client_state()
        if state is None:
            raise RuntimeError("Codex client requires a room context")
        state.selected_model = value

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        if self._retention.stopping:
            await self.cleanup_all()
        self._retention = self._retention.renewed()
        await super().on_started(agent_name, agent_description)
        self._build_system_prompt()
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
            "sandbox=%s, windows_sandbox=%s, approval_mode=%s, "
            "execution_reporting=%s, self_config_tools=%s, "
            "task_events=%s, turn_markers=%s, thought_events=%s, "
            "stream_reasoning=%s, stream_plan=%s, stream_commentary=%s, "
            "diffs=%s, token_usage=%s",
            agent_name,
            self.config.transport,
            self.config.model or "auto",
            self.config.sandbox or "default",
            self.config.windows_sandbox or "codex config",
            self.config.approval_mode,
            Emit.TOOL_CALLS in self.features.emit,
            self.config.enable_self_config_tools,
            Emit.TASK_EVENTS in self.features.emit,
            self.config.emit_turn_task_markers,
            Emit.THOUGHTS in self.features.emit,
            self.config.stream_reasoning_events,
            self.config.stream_plan_events,
            self.config.stream_commentary_events,
            self.config.emit_diff_events,
            self.config.emit_token_usage_events,
        )

    def is_control_message(self, msg: PlatformMessage) -> bool:
        """Keep all local commands outside ordinary model input."""
        return parse_command(msg.content) is not None

    async def try_handle_control(
        self, msg: PlatformMessage, tools: AgentToolsProtocol
    ) -> bool:
        command = parse_command(msg.content)
        if command is None:
            return False
        room_id = msg.room_id
        if command[0] in APPROVAL_COMMANDS:
            # Resolves a decision its turn already owns: no room or process.
            try:
                handled = await self._handle_approval_command(
                    tools=tools,
                    msg=msg,
                    room_id=room_id,
                    command=command[0],
                    args=command[1],
                )
            except DeliveryFailedError as e:
                reraise_delivery_cause(e)
            if handled:
                tools.turn.settle()
            return handled
        if command[0] != CodexCommand.STATUS:
            return False
        self._active_room.set(room_id)
        await self._handle_local_command(
            tools=tools, msg=msg, room_id=room_id, command=command[0], args=command[1]
        )
        tools.turn.settle()
        return True

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Handle one turn over its original messages."""
        await self._admit_turn(room_id, "Codex", tools)
        room = self._room_client(room_id)
        self._active_room.set(room_id)
        # A local command is always a turn of its own (see is_control_message).
        command = parse_command(messages[0].content) if len(messages) == 1 else None

        release: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        owner_ready: asyncio.Future[TurnTask] = (
            asyncio.get_running_loop().create_future()
        )
        owner = start_releasable_turn(
            room.retained,
            partial(
                self._run_turn,
                owner_ready=owner_ready,
                room=room,
                messages=messages,
                tools=tools,
                history=history,
                participants_msg=participants_msg,
                contacts_msg=contacts_msg,
                is_session_bootstrap=is_session_bootstrap,
                command=command,
            ),
            tools=tools,
            room_id=room_id,
        )
        turn = owner.task
        self._turn_release[room_id] = release
        self._turn_tasks[room_id] = owner
        owner_ready.set_result(owner)
        turn.add_done_callback(lambda task: self._forget_turn(room_id, task))
        try:
            await asyncio.wait({release, turn}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await self._cancel_turn(room_id)
            raise
        finally:
            if self._turn_release.get(room_id) is release:
                del self._turn_release[room_id]
        if turn.done():
            # No human was asked: the turn's outcome is the input handler's own.
            await turn

    async def _run_turn(
        self,
        *,
        owner_ready: asyncio.Future[TurnTask],
        room: RoomCodexClient,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        is_session_bootstrap: bool,
        command: tuple[CodexCommand, str] | None,
    ) -> None:
        """Run one turn to completion, leasing its room's process throughout."""
        owner = await owner_ready
        async with room.rpc_lock:
            try:
                if command is not None and await self._run_local_command(
                    room=room, msg=messages[0], tools=tools, command=command
                ):
                    return
                async with (
                    room.retained.use(
                        prepare=partial(room.retain_system_context, contacts_msg)
                    ),
                    detached_turn(tools, room_id=room.room_id),
                ):
                    try:
                        await self._serve_turn(
                            owner=owner,
                            room=room,
                            messages=messages,
                            tools=tools,
                            history=history,
                            participants_msg=participants_msg,
                            is_session_bootstrap=is_session_bootstrap,
                        )
                    finally:
                        self._abandon_unseeded_thread(room)
            except DeliveryFailedError as e:
                reraise_delivery_cause(e)
            except (
                TurnResultAlreadyReported,
                TerminalProviderFailure,
                InputNotAccepted,
            ):
                # A lease refused by a closing room is no failed turn: the
                # host keeps the message.
                raise
            except (CodexJsonRpcError, CodexContinuityError) as e:
                # A structured RPC error from the app-server (e.g. "model not
                # available") or a refused restore is safe, curated text --
                # unlike an arbitrary caught exception, worth showing verbatim.
                await tools.send_failure(AgentFailure(CODEX_PROVIDER, str(e)))
                raise
            except TimeoutError as timeout:
                # Only waiting on the room's process start or stop raises
                # this; an abandoned start stays owned until recovery closes it.
                logger.warning(
                    "Room %s: Codex process not ready", room.room_id, exc_info=timeout
                )
                await tools.send_failure(
                    AgentFailure(CODEX_PROVIDER, PROCESS_NOT_READY_ERROR)
                )
                raise
            except Exception:
                logger.exception(
                    "Unexpected error in Codex turn (room=%s)", room.room_id
                )
                await tools.send_failure(
                    AgentFailure(CODEX_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                )
                raise

    async def _run_local_command(
        self,
        *,
        room: RoomCodexClient,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        command: tuple[CodexCommand, str],
    ) -> bool:
        """Handle a local command, leasing the room's process only for those
        that ask it: the rest must work while it cannot start, as
        ``/thread archive`` must after Codex refuses to restore the thread."""
        name, args = command
        lease = room.retained.use() if name in PROCESS_COMMANDS else nullcontext()
        async with lease:
            handled = await self._handle_local_command(
                tools=tools, msg=msg, room_id=room.room_id, command=name, args=args
            )
        if handled:
            tools.turn.settle()
        return handled

    async def _serve_turn(
        self,
        *,
        owner: TurnTask,
        room: RoomCodexClient,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        participants_msg: str | None,
        is_session_bootstrap: bool,
    ) -> None:
        room_id = room.room_id
        # The turn keeps its originals, and so its audience, in this frame
        # through detached approval waits.
        audience = self._turn_audience(messages)
        contacts_msg = "\n".join(room.pending_system_context) or None
        thread_id = await self._ensure_thread(
            room=room,
            history=history,
            tools=tools,
            is_session_bootstrap=is_session_bootstrap,
        )
        seed = room.seed
        replay = (
            await self._replay_history(
                room=room,
                messages=messages,
                tools=tools,
                history=history,
                is_session_bootstrap=is_session_bootstrap,
            )
            if seed.replay_due
            else []
        )
        if seed.replay_due and participants_msg is None:
            # The runtime sends the roster only when it changes, so a thread
            # abandoned unseeded may have taken the latest one with it.
            participants_msg = build_participants_message(tools.participants)
        turn_input, has_pending_prompt_injection = self._build_turn_input(
            messages=messages,
            participants_msg=participants_msg,
            contacts_msg=contacts_msg,
            room_id=room_id,
            replay=replay,
        )
        turn_params: dict[str, Any] = {"threadId": thread_id, "input": turn_input}
        self._apply_turn_overrides(turn_params, room_id=room_id)

        async with (
            self.backend_turns.running(room_id),
            self._native_turn(room, tools, owner) as native,
        ):
            await self.backend_turns.started(
                room_id, self._reveal_codex_turn(room, native, turn_params, thread_id)
            )
            if has_pending_prompt_injection:
                self._prompt_injected_rooms.add(room_id)
            await self._emit_turn_started(
                tools=tools,
                messages=messages,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=native.turn_id,
            )

            usage_obj = self._token_usage.get(thread_id)
            if usage_obj is not None:
                usage_obj.reset_turn_deltas()

            # perf_counter (not monotonic): highest-resolution clock, so a fast
            # turn still measures a non-zero duration on Windows, where
            # monotonic()'s coarse tick can round an instant turn to 0.0.
            turn_start = _time.perf_counter()
            try:
                result = await self._process_turn_events(
                    native=native,
                    tools=tools,
                    audience=audience,
                    room_id=room_id,
                    thread_id=thread_id,
                    turn_start=turn_start,
                )
            except (TurnResultAlreadyReported, *HOST_CONTROL_ERRORS):
                # Host control and already-reported failures belong to the
                # runtime, not to the provider-failure fallback.
                raise
            except CodexJsonRpcError as error:
                await self._emit_failed_turn_outcome(
                    tools=tools,
                    audience=audience,
                    room_id=room_id,
                    thread_id=thread_id,
                    turn_id=native.turn_id,
                    result=TurnResult(
                        turn_status=CodexTurnStatus.FAILED, turn_error=str(error)
                    ),
                    turn_start=turn_start,
                )
                raise
            except Exception as error:
                # The event reader already logged the failure that lost its process.
                if error is not native.loss:
                    logger.exception(
                        "Unexpected error during Codex turn event processing "
                        "(thread=%s, turn=%s)",
                        thread_id,
                        native.turn_id,
                    )
                await tools.send_failure(
                    AgentFailure(CODEX_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
                )
                await self._emit_failed_turn_outcome(
                    tools=tools,
                    audience=audience,
                    room_id=room_id,
                    thread_id=thread_id,
                    turn_id=native.turn_id,
                    result=TurnResult(
                        turn_status=CodexTurnStatus.FAILED,
                        turn_error=TURN_PROCESSING_ERROR,
                    ),
                    turn_start=turn_start,
                )
                raise TurnResultAlreadyReported(TURN_PROCESSING_ERROR) from None
        if result.completed:
            seed.confirm()
            if not seed.replay_due:
                room.pending_system_context.clear()
        if not room.recorded:
            room.recorded = await CODEX_RESUME_RECORD.post(
                tools,
                room_id=room_id,
                seed=seed,
                session=thread_id,
                details={CodexMetadataKey.TRANSPORT: self.config.transport},
            )
        await self._emit_turn_outcome(
            tools=tools,
            audience=audience,
            room_id=room_id,
            thread_id=thread_id,
            turn_id=native.turn_id,
            turn_status=result.turn_status,
            turn_error=result.turn_error,
            native_messages=result.native_messages,
            duration_s=_time.perf_counter() - turn_start,
        )

    async def _emit_turn_started(
        self,
        *,
        tools: AgentToolsProtocol,
        messages: Sequence[PlatformMessage],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
    ) -> None:
        if Emit.TASK_EVENTS not in self.features.emit:
            return
        if self.config.emit_turn_task_markers:
            await send_event_safe(
                tools,
                content=task_event_content(
                    task_id=turn_id,
                    task="Codex turn",
                    status="started",
                    summary=f"Thread: {thread_id}",
                ),
                message_type="task",
                metadata={
                    CodexMetadataKey.THREAD: thread_id,
                    CodexMetadataKey.TURN: turn_id,
                    CodexMetadataKey.ROOM: room_id,
                },
                log_label="turn started task event",
                log_level=logging.DEBUG,
            )
        if self.config.emit_turn_lifecycle_events:
            input_summary = "\n".join(m.content or "" for m in messages)[:200]
            await send_event_safe(
                tools,
                content=task_event_content(
                    task_id=turn_id,
                    task="Codex turn lifecycle",
                    status="started",
                    summary=f"Thread: {thread_id}",
                ),
                message_type="task",
                metadata={
                    "codex_event_type": "turn_lifecycle",
                    CodexMetadataKey.ROOM: room_id,
                    CodexMetadataKey.THREAD: thread_id,
                    CodexMetadataKey.TURN: turn_id,
                    "codex_turn_status": "started",
                    "codex_input_summary": input_summary,
                },
                log_label="turn started lifecycle event",
                log_level=logging.DEBUG,
            )

    def _release_turn(self, room_id: str, tools: AgentToolsProtocol) -> None:
        """Release the input handler while the turn waits for a human reply."""
        release = self._turn_release.get(room_id)
        if release is not None and not release.done():
            tools.turn.detach()
            release.set_result(None)

    def _forget_turn(self, room_id: str, task: asyncio.Task[None]) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None and owner.task is task:
            del self._turn_tasks[room_id]

    async def _emit_failed_turn_outcome(
        self,
        *,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        result: TurnResult,
        turn_start: float,
    ) -> None:
        """Emit failure lifecycle events without posting a second reply."""
        await self._emit_turn_outcome(
            tools=tools,
            audience=audience,
            room_id=room_id,
            thread_id=thread_id,
            turn_id=turn_id,
            turn_status=result.turn_status,
            turn_error=result.turn_error,
            native_messages=result.native_messages,
            duration_s=_time.perf_counter() - turn_start,
            include_reply=False,
        )

    async def _process_turn_events(
        self,
        *,
        native: NativeTurn,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        thread_id: str,
        turn_start: float,
    ) -> TurnResult:
        """Consume the events addressed to one native turn and return its result."""
        turn_id = native.turn_id
        ref = TurnRef(room_id, thread_id, turn_id)
        result = TurnResult()
        failure_reported = False
        try:
            while True:
                _remaining = max(
                    0.0,
                    self.config.turn_timeout_s - (_time.perf_counter() - turn_start),
                )
                event = await asyncio.wait_for(native.events.get(), timeout=_remaining)
                if isinstance(event, Exception):
                    raise event
                if event.is_request:
                    await self._answer_native_request(
                        native,
                        tools=tools,
                        audience=audience,
                        room_id=room_id,
                        event=event,
                    )
                    continue

                params = event_params(event)

                if event.method in RAW_TASK_METHODS:
                    if Emit.TASK_EVENTS in self.features.emit:
                        await raw_task_event(
                            tools, ref, event.method, params, self._task_titles
                        )
                    continue

                if event.method == "error":
                    reported = await error_notification(tools, ref, params)
                    failure_reported = failure_reported or reported
                    continue

                # --- Phase 3: Real-time streaming ---
                if event.method in {
                    "item/reasoning/summaryTextDelta",
                    "item/reasoning/textDelta",
                }:
                    if self.config.stream_reasoning_events:
                        delta = params.get("delta", "")
                        item_id = str(params.get("itemId") or "")
                        await send_event_safe(
                            tools,
                            content=str(delta),
                            message_type="thought",
                            metadata={
                                "streaming": True,
                                "codex_item_id": item_id,
                                "codex_event_type": event.method,
                                **ref.metadata(),
                            },
                            log_label="reasoning delta",
                            log_level=logging.DEBUG,
                        )
                    continue

                if event.method == "item/plan/delta":
                    if self.config.stream_plan_events:
                        delta = params.get("delta", "")
                        item_id = str(params.get("itemId") or "")
                        await send_event_safe(
                            tools,
                            content=str(delta),
                            message_type="thought",
                            metadata={
                                "streaming": True,
                                "subtype": "plan",
                                "codex_item_id": item_id,
                                **ref.metadata(),
                            },
                            log_label="plan delta",
                            log_level=logging.DEBUG,
                        )
                    continue

                # --- Phase 2: Plan step tracking ---
                if event.method == "turn/plan/updated":
                    if self.config.stream_plan_events:
                        await plan_steps(tools, ref, params)
                    continue

                # --- Phase 4: Token usage ---
                if event.method == "thread/tokenUsage/updated":
                    self._update_token_usage(thread_id, params)
                    if self.config.emit_token_usage_events:
                        await token_usage(tools, ref, self._token_usage.get(thread_id))
                    continue

                # --- Phase 2: Context compaction events ---
                if event.method == "context/compacted":
                    if (
                        self.config.emit_turn_lifecycle_events
                        and Emit.TASK_EVENTS in self.features.emit
                    ):
                        compacted_thread = str(params.get("threadId") or thread_id)
                        compacted_turn = str(params.get("turnId") or turn_id or "")
                        await send_event_safe(
                            tools,
                            content=task_event_content(
                                task_id=compacted_turn or None,
                                task="Codex context compaction",
                                status="completed",
                                summary=f"Thread: {compacted_thread}",
                            ),
                            message_type="task",
                            metadata={
                                "codex_event_type": "context_compaction",
                                CodexMetadataKey.ROOM: room_id,
                                CodexMetadataKey.THREAD: compacted_thread,
                                CodexMetadataKey.TURN: compacted_turn or None,
                            },
                            log_label="context compaction event",
                            log_level=logging.DEBUG,
                        )
                    continue

                # --- Phase 4: Aggregated diffs ---
                if event.method == "turn/diff/updated":
                    if (
                        self.config.emit_diff_events
                        and Emit.TASK_EVENTS in self.features.emit
                    ):
                        await diff_updated(tools, ref, params)
                    continue

                if event.method == CodexNotificationMethod.ITEM_STARTED:
                    item = params.get("item")
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == CodexItemType.AGENT_MESSAGE:
                        result.native_messages[str(item["id"])] = NativeMessage(
                            phase=item.get("phase")
                        )
                    continue

                if event.method == "item/agentMessage/delta":
                    item_id = str(params.get("itemId") or "")
                    message = result.native_messages.get(item_id)
                    delta = params.get("delta")
                    if (
                        message is not None
                        and message.phase == "commentary"
                        and isinstance(delta, str)
                        and self.config.stream_commentary_events
                        and Emit.THOUGHTS in self.features.emit
                    ):
                        delivered = await send_event_safe(
                            tools,
                            content=delta,
                            message_type="thought",
                            metadata={
                                "streaming": True,
                                "subtype": "commentary",
                                "codex_item_id": item_id,
                                **ref.metadata(),
                            },
                            log_label="commentary delta",
                            log_level=logging.DEBUG,
                        )
                        if delivered:
                            message.streamed_text += delta
                    continue

                if event.method == CodexNotificationMethod.ITEM_COMPLETED:
                    item = params.get("item")
                    if isinstance(item, dict):
                        if item.get("type") == CodexItemType.AGENT_MESSAGE:
                            message = result.native_messages.setdefault(
                                str(item["id"]), NativeMessage()
                            )
                            message.phase = item.get("phase")
                            text = item.get("text")
                            if isinstance(text, str):
                                message.completed_text = text
                        else:
                            await item_completed(tools, ref, item, self.features.emit)
                    continue

                if event.method == TRANSPORT_CLOSED:
                    result.turn_status = CodexTurnStatus.FAILED
                    result.turn_error = TRANSPORT_LOST_ERROR
                    # Skipped when an earlier "error" notification in this same
                    # turn already reported one, so one incident isn't posted
                    # twice -- but the turn still fails either way.
                    if not failure_reported:
                        await tools.send_failure(
                            AgentFailure(
                                CODEX_PROVIDER,
                                result.turn_error,
                                CODEX_TRANSPORT_CLOSED_FAILURE_CODE,
                            )
                        )
                    raise TurnResultAlreadyReported(result.turn_error)

                if event.method == "turn/completed":
                    turn_payload = (
                        params.get("turn")
                        if isinstance(params.get("turn"), dict)
                        else {}
                    )
                    result.turn_status = str(
                        turn_payload.get("status") or CodexTurnStatus.FAILED
                    )
                    result.turn_error = turn_error_message(turn_payload)
                    if result.turn_status != CodexTurnStatus.FAILED:
                        break
                    # Skipped when an earlier "error" notification in this same
                    # turn already reported one, so one incident isn't posted
                    # twice -- but the turn still fails either way.
                    if not failure_reported:
                        await turn_failed(tools, ref, turn_payload)
                    raise TurnResultAlreadyReported(result.turn_error or "Turn failed")
        except TurnResultAlreadyReported as error:
            result.turn_status = CodexTurnStatus.FAILED
            result.turn_error = str(error)
            await self._emit_failed_turn_outcome(
                tools=tools,
                audience=audience,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=turn_id,
                result=result,
                turn_start=turn_start,
            )
            raise
        except TimeoutError:
            logger.error(
                "Codex turn timed out after %ss (thread=%s, turn=%s)",
                self.config.turn_timeout_s,
                thread_id,
                turn_id,
            )
            try:
                await native.client.request(
                    CodexRequestMethod.TURN_INTERRUPT,
                    {"threadId": thread_id, "turnId": turn_id},
                )
            except Exception:
                logger.warning(
                    "Failed to send turn/interrupt after timeout", exc_info=True
                )
            timeout_message = (
                f"Codex turn timed out after {self.config.turn_timeout_s}s"
            )
            await tools.send_failure(
                AgentFailure(CODEX_PROVIDER, timeout_message, FAILURE_CODE_TIMEOUT)
            )
            result.turn_status = CodexTurnStatus.FAILED
            result.turn_error = timeout_message
            # A raise from here lands in this try's own except clauses, not the
            # sibling `except TurnResultAlreadyReported` above -- Python never
            # matches an exception raised inside one except against a sibling
            # of the same try. Call the same failed-turn telemetry directly
            # (as the input handler's CodexJsonRpcError branch already does) so a
            # timeout still gets the "Codex turn"/lifecycle failed-status
            # events every other TurnResultAlreadyReported path emits.
            await self._emit_failed_turn_outcome(
                tools=tools,
                audience=audience,
                room_id=room_id,
                thread_id=thread_id,
                turn_id=turn_id,
                result=result,
                turn_start=turn_start,
            )
            raise TurnResultAlreadyReported(timeout_message)
        return result

    async def _answer_native_request(
        self,
        native: NativeTurn,
        *,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        event: RpcEvent,
    ) -> None:
        """Answer the turn's request while its process can still read the
        answer; a lost process's requests go neither to the room nor to it."""
        if native.lost:
            return
        try:
            await self._handle_server_request(
                client=native.client,
                tools=tools,
                audience=audience,
                room_id=room_id,
                event=event,
            )
        except ConnectionError:
            if not native.lost:
                raise

    async def on_cleanup(self, room_id: str) -> None:
        """Leave a room: admission closes at once, its turn gets
        ``turn_settle_timeout_s`` to wind down, then its process closes and the
        room is forgotten.

        Unfinished or failed cleanup keeps the room registered, refusing work,
        with its process, thread and workspace claim for a retry to join.
        """
        room = self._room_clients.get(room_id)
        if room is None:
            return
        self._approvals.decline_all(room_id)
        await room.retained.dispose()

    async def cleanup_all(self) -> None:
        """Stop the residency owner: admission closes at once, every room's
        turn gets ``turn_settle_timeout_s`` to wind down, then its process
        closes, all reported within one ``retention.transition_timeout_s``.

        Callers join the same stop, and a failed one is retried. An unfinished
        or failed stop keeps every room fenced with its process, thread and
        workspace. Success stays sealed until ``on_started``.
        """
        for room_id in list(self._room_clients):
            self._approvals.decline_all(room_id)
        await self._retention.stop()
        await self.backend_turns.drain()

    async def _acquire(
        self, room: RoomCodexClient, generation: int, resume: bool
    ) -> None:
        """Start the room's process and strictly restore the thread it retains.

        A crash keeps the thread id and the room's model, so ``resume``
        changes nothing: a retained thread is always restored on its model.
        The reader runs before ``thread/resume``, which can start native work.
        """
        room.client = client = self._build_client(room.workspace)
        await client.connect()
        room.reader = spawn_owned(
            self._read_native_events(room, client, generation),
            label=f"Room {room.room_id} Codex event reader",
        )
        initialized = await client.initialize(
            client_name=self.config.client_name,
            client_title=self.config.client_title,
            client_version=self.config.client_version,
            experimental_api=self.config.experimental_api,
        )
        await self._check_windows_sandbox(client, initialized)
        await self._register_skill_roots(client)
        if room.selected_model is None:
            room.selected_model = await self._select_model(client)
            logger.info(
                "Room %s: Codex model selected: %s", room.room_id, room.selected_model
            )
        if (thread_id := self._room_threads.get(room.room_id)) is not None:
            await self._restore_thread(room, client, thread_id)

    def _resume_params(self, room: RoomCodexClient, thread_id: str) -> dict[str, Any]:
        """``thread/resume`` overrides pinning the thread to the room's workspace.

        Without ``cwd`` Codex reports the thread's recorded cwd, which a later
        strict restore would see as a changed setting.
        """
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": room.workspace,
            "personality": self.config.personality,
        }
        self._apply_thread_sandbox(params, room_id=room.room_id)
        return params

    async def _restore_thread(
        self, room: RoomCodexClient, client: CodexClientProtocol, thread_id: str
    ) -> None:
        """Resume exactly ``thread_id``; refusal keeps it for the next attempt.

        Sends only overrides ``thread/resume`` supports -- never ``path``,
        ``history`` or tools, which resume silently ignores -- and never
        starts a new thread in its place.
        """
        result = await client.request(
            CodexRequestMethod.THREAD_RESUME, self._resume_params(room, thread_id)
        )
        resumed = result_entry_id(result, "thread")
        if resumed != thread_id:
            raise CodexContinuityError(
                f"Codex resumed thread {resumed or 'none'!r} instead of {thread_id!r}"
            )
        if changes := room.settings.changes_in(ThreadSettings.reported_by(result)):
            raise CodexContinuityError(
                f"Codex restored thread {thread_id} with changed settings: "
                + "; ".join(changes)
            )
        room.restored = True

    async def _observe_command(
        self, room: RoomCodexClient, generation: int, event: RpcEvent
    ) -> None:
        if event.method not in {
            CodexNotificationMethod.ITEM_STARTED,
            CodexNotificationMethod.ITEM_COMPLETED,
        }:
            return
        params = event_params(event)
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") not in ACTIVITY_ITEM_TYPES:
            return
        identity = (
            event_thread_id(event) or "",
            event_turn_id(event) or "",
            item_call_id(item),
        )
        if item.get("type") != CodexItemType.COMMAND_EXECUTION:
            await self._observe_foreground_item(room, generation, event, identity, item)
            return
        if event.method == CodexNotificationMethod.ITEM_COMPLETED:
            if (command := room.commands.pop(identity, None)) is not None:
                command.terminal.set()
                command.wake.set()
                await end_activity(command.tools, command.token)
            return
        tools = self._native_item_tools(room, generation, event, identity)
        if identity in room.commands or tools is None:
            return
        command = CommandResource(
            tools, generation, *identity, process_id=item.get("processId")
        )
        worker = asyncio.create_task(self._wait_command(room, command))
        command.owner = bind_turn_task(
            tools, worker, role=TaskRole.RESOURCE, request_stop=command.wake.set
        )
        command.tools = resource_tools(tools, command.owner)
        room.commands[identity] = command
        name = tool_item(CodexItemType.COMMAND_EXECUTION, item).name
        await start_native_tool_activity(command.tools, command.token, name)

    async def _observe_foreground_item(
        self,
        room: RoomCodexClient,
        generation: int,
        event: RpcEvent,
        identity: tuple[str, str, str],
        item: dict[str, Any],
    ) -> None:
        if event.method == CodexNotificationMethod.ITEM_COMPLETED:
            if (current := room.item_activity.pop(identity, None)) is not None:
                await end_activity(current[0], current[1])
            return
        tools = self._native_item_tools(room, generation, event, identity)
        if identity in room.item_activity or tools is None:
            return
        token = object()
        room.item_activity[identity] = (tools, token)
        await self._show_item_started(tools, item, token)

    @staticmethod
    def _native_item_tools(
        room: RoomCodexClient,
        generation: int,
        event: RpcEvent,
        identity: tuple[str, str, str],
    ) -> AgentToolsProtocol | None:
        native = room.turn
        if (
            not all(identity)
            or native is None
            or native.tools is None
            or native.generation != generation
            or not native.owns(event)
            or native.thread_id != identity[0]
            or not room.retained.has_authority(generation)
        ):
            return None
        return native.tools

    async def _wait_command(
        self, room: RoomCodexClient, command: CommandResource
    ) -> None:
        await command.wake.wait()
        if not command.terminal.is_set():
            await self._stop_command(room, command)

    async def _stop_command(
        self, room: RoomCodexClient, command: CommandResource
    ) -> None:
        if command.terminal.is_set():
            return
        if (
            room.commands.get(command.identity) is command
            and room.generation == command.generation
        ):
            try:
                if not self.config.experimental_api or command.process_id is None:
                    raise CleanupIncompleteError(
                        "Codex command has no supported selective stop"
                    )
                if command.owner is None:
                    raise CleanupIncompleteError(
                        "Codex command admission was interrupted"
                    )
                async with asyncio.timeout_at(command.owner.deadline):
                    await room.require_client().request(
                        CodexRequestMethod.TERMINAL_TERMINATE,
                        {
                            "threadId": command.thread_id,
                            "processId": command.process_id,
                        },
                    )
                    await command.terminal.wait()
                    return
            except Exception as error:
                logger.warning(
                    "Room %s: native command cleanup requires recovery",
                    room.room_id,
                    exc_info=True,
                )
                self._lose_process(room, command.generation, error)
        await command.closed.wait()

    @staticmethod
    def _revoke_commands(room: RoomCodexClient, generation: int) -> None:
        for command in tuple(room.commands.values()):
            if command.generation == generation and command.owner is not None:
                command.owner.terminate()

    async def _drain_commands(self, room: RoomCodexClient) -> None:
        await drain_turn_tasks(
            command.owner
            for command in room.commands.values()
            if command.owner is not None
        )
        if room.commands and self.config.transport == "websocket":
            raise CleanupIncompleteError(
                "Codex remote commands have not confirmed termination"
            )

    async def _read_native_events(
        self, room: RoomCodexClient, client: CodexClientProtocol, generation: int
    ) -> None:
        """The process's one event reader, from connect until release.

        An event goes to the Band turn that started its native turn. Work no
        Band turn started -- a late item, an autonomous native turn -- is
        refused rather than handled with another turn's tools.
        """
        while True:
            try:
                event = await client.recv_event()
            except Exception as error:
                logger.exception(
                    "Room %s: Codex event reader failed; recovering generation %s",
                    room.room_id,
                    generation,
                )
                self._lose_process(room, generation, error)
                return
            if event.method == TRANSPORT_CLOSED:
                logger.warning(
                    "Room %s: Codex transport closed (%s); recovering generation %s",
                    room.room_id,
                    loss_reason(event),
                    generation,
                )
                self._lose_process(room, generation, event)
                return
            async with room.routing_lock:
                await self._observe_command(room, generation, event)
                unowned = self._route_native_event(room, generation, event)
            if unowned is not None:
                await self._refuse_unowned(room, client, unowned)

    @staticmethod
    def _route_native_event(
        room: RoomCodexClient, generation: int, event: RpcEvent
    ) -> RpcEvent | None:
        """Hand ``event`` to its Band turn; return it when no turn owns it."""
        native = room.turn
        if native is None or native.generation != generation:
            return event
        if native.turn_id is None:
            native.early.append(event)
            return None
        return None if native.claim(event) else event

    def _lose_process(
        self, room: RoomCodexClient, generation: int, cause: RpcEvent | Exception
    ) -> None:
        """Hand a process whose events stopped to recovery, and end the turn
        waiting on them with ``cause``: the transport's closing notice, or the
        reader's own failure. Recovery closes the process once that turn unwinds.
        """
        room.retained.recover(
            generation=generation, reason=f"Codex events stopped: {loss_reason(cause)}"
        )
        self._revoke_commands(room, generation)
        native = room.turn
        if native is not None and native.generation == generation:
            native.loss = cause
            native.owner.fail(
                cause
                if isinstance(cause, Exception)
                else ProviderRunError.session_lost(),
                cooperative=True,
            )
            # Approvals the lost process asked for can never be answered.
            self._approvals.decline_all(room.room_id)
            native.events.put_nowait(native.loss)

    async def _refuse_unowned(
        self, room: RoomCodexClient, client: CodexClientProtocol, event: RpcEvent
    ) -> None:
        """Answer native work no Band turn started, without lending it one's tools."""
        if not event.is_request or event.id is None:
            logger.debug(
                "Room %s: no Band turn owns Codex %s; dropped",
                room.room_id,
                event.method,
            )
            return
        logger.warning(
            "Room %s: refusing Codex %s from native turn %s, which no Band turn started",
            room.room_id,
            event.method,
            event_turn_id(event),
        )
        try:
            if event.method == DYNAMIC_TOOL_CALL_METHOD:
                await client.respond(
                    event.id, text_tool_result(UNOWNED_TOOL_CALL_TEXT, success=False)
                )
            elif event.method in CODEX_APPROVAL_METHODS:
                await client.respond(event.id, approval_response("decline"))
            else:
                await refuse_unhandled_request(
                    client, request_id=event.id, method=event.method
                )
        except Exception:
            logger.warning(
                "Room %s: could not refuse Codex %s",
                room.room_id,
                event.method,
                exc_info=True,
            )

    async def _interrupt_codex_turn(self, turn: CodexTurn) -> None:
        try:
            await turn.client.request(
                CodexRequestMethod.TURN_INTERRUPT,
                {"threadId": turn.thread_id, "turnId": turn.turn_id},
            )
        finally:
            # An interrupt response does not prove native tool work has stopped.
            for room in self._room_clients.values():
                if room.client is turn.client:
                    self.backend_turns.recovering(
                        room.room_id, turn, room.retained, generation=room.generation
                    )
                    break

    @asynccontextmanager
    async def _native_turn(
        self, room: RoomCodexClient, tools: AgentToolsProtocol, owner: TurnTask
    ) -> AsyncIterator[NativeTurn]:
        """Bind the native turn this Band turn starts to it until the turn ends.

        Whatever is still undelivered then belongs to no Band turn.
        """
        room.turn = native = NativeTurn(
            client=room.require_client(),
            generation=room.generation,
            tools=tools,
            owner=owner,
        )
        try:
            yield native
        except asyncio.CancelledError:
            # A cancelled start may never reveal a handle to interrupt.
            self.backend_turns.recovering(
                room.room_id,
                CodexTurn(native.client, native.thread_id or "", native.turn_id or ""),
                room.retained,
                generation=native.generation,
            )
            if native.owner.failure is not None:
                raise native.owner.failure from None
            raise
        finally:
            room.turn = None
            for identity, activity in tuple(room.item_activity.items()):
                if identity[:2] == (native.thread_id, native.turn_id):
                    del room.item_activity[identity]
                    await end_activity(activity[0], activity[1])
            await self._refuse_undelivered(room, native)

    async def _refuse_undelivered(
        self, room: RoomCodexClient, native: NativeTurn
    ) -> None:
        """Refuse a finished turn's leftovers, within the turn's settle bound so
        a process that stopped reading cannot hold the room's turn open."""
        if native.lost:
            # The process can never read a refusal.
            return
        try:
            async with asyncio.timeout(self.config.turn_settle_timeout_s):
                for event in undelivered_events(native):
                    await self._refuse_unowned(room, native.client, event)
        except TimeoutError:
            logger.warning(
                "Room %s: Codex stopped reading; left native requests unanswered",
                room.room_id,
            )

    async def _reveal_codex_turn(
        self,
        room: RoomCodexClient,
        native: NativeTurn,
        params: dict[str, Any],
        thread_id: str,
    ) -> CodexTurn:
        await self._start_native_turn(room, native, params)
        return CodexTurn(room.require_client(), thread_id, native.turn_id or "")

    async def _start_native_turn(
        self, room: RoomCodexClient, native: NativeTurn, params: dict[str, Any]
    ) -> None:
        """Start the native turn and claim the events that arrived before its id."""
        native.thread_id = params["threadId"]
        started = await native.client.request(CodexRequestMethod.TURN_START, params)
        turn_id = result_entry_id(started, "turn")
        if not turn_id:
            raise RuntimeError("Codex turn/start returned no turn id")
        async with room.routing_lock:
            native.turn_id = turn_id
            early, native.early = native.early, []
            refused = []
            for event in early:
                await self._observe_command(room, native.generation, event)
                if not native.claim(event):
                    refused.append(event)
        for event in refused:
            await self._refuse_unowned(room, native.client, event)

    async def _release(
        self, room: RoomCodexClient, cause: ReleaseCause
    ) -> ReleaseOutcome:
        """Close the room's process; its thread id stays for the next process."""
        await self._drain_commands(room)
        await room.stop_reader()
        if room.client is not None:
            timeout = self.config.client_close_timeout_s
            try:
                await asyncio.wait_for(room.client.close(), timeout=timeout)
            except TimeoutError as error:
                # This becomes the leave's error, so it names what overran.
                raise TimeoutError(
                    f"Codex process for room {room.room_id} did not close "
                    f"within {timeout}s"
                ) from error
        commands = tuple(room.commands.values())
        for command in commands:
            command.closed.set()
            command.terminal.set()
            command.wake.set()
            await end_activity(command.tools, command.token)
        if commands:
            await asyncio.gather(
                *(
                    command.owner.task
                    for command in commands
                    if command.owner is not None
                )
            )
        room.commands.clear()
        room.client = None
        room.restored = False
        return ReleaseOutcome.RELEASED

    async def _dispose(self, room: RoomCodexClient) -> None:
        """Erase what only a final leave ends: thread, settings, workspace claim."""
        room_id = room.room_id
        self._forget_room_thread(room_id)
        self._approvals.forget_room(room_id)
        self._sandbox_overrides.pop(room_id, None)
        self._task_titles.forget_room(room_id)
        self._workspaces.release(room_id, room.workspace)
        room.pending_system_context.clear()
        if self._room_clients.get(room_id) is room:
            del self._room_clients[room_id]
        self.backend_turns.confirm_disposed(room_id)

    def _forget_room_thread(self, room_id: str) -> str | None:
        """Drop the room's thread mapping and per-thread state; return the thread id."""
        thread_id = self._room_threads.pop(room_id, None)
        if thread_id is not None:
            self._token_usage.pop(thread_id, None)
        self._prompt_injected_rooms.discard(room_id)
        return thread_id

    def _abandon_unseeded_thread(self, room: RoomCodexClient) -> None:
        """Forget a fresh thread whose seeding turn did not complete.

        Band history never named it, so the next turn starts a new thread and
        replays the kept transcript into it.
        """
        if room.seed.replay_due and (
            thread_id := self._forget_room_thread(room.room_id)
        ):
            logger.info(
                "Room %s: abandoning Codex thread %s; its seeding turn did not "
                "complete",
                room.room_id,
                thread_id,
            )
            room.forget_thread()

    async def _check_windows_sandbox(
        self, client: CodexClientProtocol, initialized: dict[str, Any]
    ) -> None:
        """Warn once when a Windows app-server has no backend for the sandbox it must enforce."""
        if (
            self._windows_sandbox_checked
            or self.config.windows_sandbox is not None
            or initialized.get("platformOs") != WINDOWS_PLATFORM_OS
            or not self._enforces_sandbox()
        ):
            return
        self._windows_sandbox_checked = True
        try:
            readiness = await client.request(
                CodexRequestMethod.WINDOWS_SANDBOX_READINESS
            )
        except CodexJsonRpcError as exc:
            logger.debug("Codex Windows sandbox readiness unavailable: %s", exc)
            return
        match readiness.get("status"):
            case CodexWindowsSandboxReadiness.NOT_CONFIGURED:
                logger.warning(WINDOWS_SANDBOX_MISSING_WARNING)
            case CodexWindowsSandboxReadiness.UPDATE_REQUIRED:
                logger.warning(WINDOWS_SANDBOX_SETUP_WARNING)

    def _enforces_sandbox(self) -> bool:
        """Whether Codex enforces the configured sandbox itself; its default is enforced."""
        policy = self.config.sandbox_policy
        kind = policy.get("type") if policy is not None else self.config.sandbox
        if not isinstance(kind, str):
            return True
        return canonical_sandbox_key(kind) in ENFORCED_SANDBOX_MODES

    async def _register_skill_roots(self, client: CodexClientProtocol) -> None:
        roots = self.config.skill_roots
        if not roots:
            return
        try:
            await client.request(
                CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET, {"extraRoots": roots}
            )
        except CodexJsonRpcError as exc:
            raise RuntimeError(f"Codex rejected skill_roots {roots}: {exc}") from exc

    def _build_client(self, workspace: str) -> CodexClientProtocol:
        return CodexStdioClient(
            command=self.config.codex_command, cwd=workspace, env=self.config.codex_env
        )

    async def _select_model(self, client: CodexClientProtocol) -> str:
        if self.config.model:
            return self.config.model

        try:
            result = await client.request(CodexRequestMethod.MODEL_LIST, {})
        except Exception:
            logger.warning(
                "model/list failed; using default Codex model", exc_info=True
            )
            return OPENAI_MODEL

        if models := visible_model_ids(result):
            return models[0]
        logger.warning("model/list has no visible models; using default Codex model")
        return OPENAI_MODEL

    async def _ensure_thread(
        self,
        *,
        room: RoomCodexClient,
        history: CodexSessionState,
        tools: AgentToolsProtocol,
        is_session_bootstrap: bool,
    ) -> str:
        """The room's thread: the retained one, one Band history names, or new."""
        room_id = room.room_id
        if (thread_id := self._room_threads.get(room_id)) is not None:
            if room.restored:
                self._confirm_restored_tools(room, tools, thread_id)
            return thread_id
        client = room.require_client()
        if is_session_bootstrap and (named := history.room_to_thread.get(room_id)):
            resumed = await self._resume_from_history(
                room=room, client=client, named=named, tools=tools
            )
            if resumed is not None:
                return resumed
        return await self._start_thread(room=room, client=client, tools=tools)

    def _confirm_restored_tools(
        self, room: RoomCodexClient, tools: AgentToolsProtocol, thread_id: str
    ) -> None:
        """Refuse a turn whose tools differ from the restored thread's.

        ``thread/resume`` restores the tools a thread was started with and
        ignores any others, so new ones would silently never reach the model.
        """
        if room.registration is not None:
            offered = tools_by_name(self._build_dynamic_tools(tools))
            registered = tools_by_name(room.registration)
            if offered != registered:
                changed = sorted(offered.keys() ^ registered.keys())
                raise CodexContinuityError(
                    f"Codex thread {thread_id} keeps the tools it was started "
                    f"with; this turn's tools differ ({', '.join(changed) or 'schemas'}). "
                    f"Send `/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}` "
                    "to start a new thread."
                )
        room.restored = False

    async def _resume_from_history(
        self,
        *,
        room: RoomCodexClient,
        client: CodexClientProtocol,
        named: str,
        tools: AgentToolsProtocol,
    ) -> str | None:
        """Resume the thread Band history names; None falls back to a new one."""
        room_id = room.room_id
        try:
            result = await client.request(
                CodexRequestMethod.THREAD_RESUME, self._resume_params(room, named)
            )
        except CodexJsonRpcError as exc:
            logger.warning(
                "thread/resume failed for room %s thread %s: %s", room_id, named, exc
            )
            return None
        thread_id = result_entry_id(result, "thread") or named
        self._room_threads[room_id] = thread_id
        # Band history does not record which tools the thread was started with.
        room.bind_thread(
            registration=None,
            settings=ThreadSettings.reported_by(result),
            seed=SessionSeed(restored=True),
        )
        if Emit.TASK_EVENTS in self.features.emit:
            await send_event_safe(
                tools,
                content=task_event_content(
                    task_id=thread_id,
                    task="Codex thread",
                    status="resumed",
                    summary=f"Room: {room_id}",
                ),
                message_type="task",
                metadata={
                    CodexMetadataKey.THREAD: thread_id,
                    CodexMetadataKey.ROOM: room_id,
                    "codex_resumed": True,
                },
                log_label="thread resumed task event",
                log_level=logging.DEBUG,
            )
        return thread_id

    async def _start_thread(
        self,
        *,
        room: RoomCodexClient,
        client: CodexClientProtocol,
        tools: AgentToolsProtocol,
    ) -> str:
        room_id = room.room_id
        dynamic_tools = self._build_dynamic_tools(tools)
        start_params: dict[str, Any] = {
            "model": room.selected_model,
            "cwd": room.workspace,
            "approvalPolicy": self.config.approval_policy,
            "personality": self.config.personality,
            "dynamicTools": dynamic_tools,
        }
        self._apply_thread_sandbox(start_params, room_id=room_id)

        started = await client.request(CodexRequestMethod.THREAD_START, start_params)
        thread_id = result_entry_id(started, "thread")
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")

        # The room's thread while its seeding turn runs: a seed that does not
        # complete abandons it, and Band history names it only once one does.
        self._room_threads[room_id] = thread_id
        room.bind_thread(
            registration=dynamic_tools,
            settings=ThreadSettings.reported_by(started),
            seed=SessionSeed(),
        )

        if Emit.TASK_EVENTS in self.features.emit:
            await send_event_safe(
                tools,
                content=task_event_content(
                    task_id=thread_id,
                    task="Codex thread",
                    status="mapped",
                    summary=f"Transport: {self.config.transport}",
                ),
                message_type="task",
                metadata={
                    CodexMetadataKey.THREAD: thread_id,
                    CodexMetadataKey.ROOM: room_id,
                    "codex_created_at": datetime.now(UTC).isoformat(),
                    CodexMetadataKey.TRANSPORT: self.config.transport,
                },
                log_label="thread mapped task event",
                log_level=logging.DEBUG,
            )

        return thread_id

    def _build_dynamic_tools(self, tools: AgentToolsProtocol) -> list[dict[str, Any]]:
        return dynamic_tools(
            tools.get_openai_tool_schemas(capabilities=self.features.capabilities),
            self.features,
            additional=self.config.additional_dynamic_tools,
            custom_tools=self._custom_tools,
        )

    def _build_turn_input(
        self,
        *,
        messages: Sequence[PlatformMessage],
        participants_msg: str | None,
        contacts_msg: str | None,
        room_id: str,
        replay: list[str],
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

        if participants_msg:
            items.append(
                {"type": "text", "text": f"{SYSTEM_CONTEXT_PREFIX} {participants_msg}"}
            )

        if contacts_msg:
            items.append(
                {"type": "text", "text": f"{SYSTEM_CONTEXT_PREFIX} {contacts_msg}"}
            )

        live = format_current_inputs(messages)
        if lines := replay[-self.config.max_history_messages :]:
            # The header tells the model the current inputs follow this marker.
            marker = new_message_marker()
            framed = HISTORY_REPLAY_HEADER.format(marker=marker)
            items.append({"type": "text", "text": f"{framed}\n" + "\n".join(lines)})
            live = f"{marker}\n{live}"

        items.append({"type": "text", "text": live})
        return items, injected_system_prompt

    async def _replay_history(
        self,
        *,
        room: RoomCodexClient,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: CodexSessionState,
        is_session_bootstrap: bool,
    ) -> list[str]:
        """The transcript lines a fresh thread's seeding turn replays.

        Only the bootstrap turn carries history; a thread reseeded later
        fetches it, so it also sees requests of turns that failed to seed.
        """
        if not self.config.inject_history_on_resume_failure:
            return []
        if is_session_bootstrap:
            return history.replay_messages
        try:
            transcript = await fetch_transcript(
                tools, room_id=room.room_id, messages=messages
            )
        except Exception:
            room.seed.carried = False
            logger.warning(
                "Room %s: could not fetch history to replay into a fresh session",
                room.room_id,
                exc_info=True,
            )
            return []
        return build_replay_messages(transcript)

    async def _handle_server_request(
        self,
        *,
        client: CodexClientProtocol,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        event: RpcEvent,
    ) -> None:
        """Dispatch a request the Band turn's native turn sent (tool call, approval).

        ``client`` is the process the request came from; the answer goes back
        there. Concurrency model: this coroutine mutates room-scoped adapter
        state (``_approvals``) without an explicit lock.
        It is safe because it runs only inside a turn, which holds its room's
        ``rpc_lock`` -- and every structure it touches is
        keyed (or routed via ``_active_room``) per room, so a concurrent
        turn in a different room never touches the same state.
        """
        if event.id is None:
            return

        params = event_params(event)

        if event.method == DYNAMIC_TOOL_CALL_METHOD:
            call = DynamicToolCall.from_params(params)
            # Codex-local slash commands surface their outcome in the room.
            report = (
                Emit.TOOL_CALLS in self.features.emit
                and call.name not in SILENT_REPORTING_TOOLS
            )
            await answer_tool_call(
                client,
                tools,
                request_id=event.id,
                call=call,
                custom_tools=self._custom_tools,
                report=report,
            )
            return

        if event.method in CODEX_APPROVAL_METHODS:
            await self._handle_approval_request(
                client=client,
                tools=tools,
                audience=audience,
                room_id=room_id,
                event=event,
                params=params,
            )
            return

        await refuse_unhandled_request(client, request_id=event.id, method=event.method)

    async def _handle_approval_request(
        self,
        *,
        client: CodexClientProtocol,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        event: RpcEvent,
        params: dict[str, Any],
    ) -> None:
        """Resolve a Codex approval request (command / file change).

        Splits off from :meth:`_handle_server_request` for readability.
        Same concurrency guarantees apply — see the docstring there.
        """
        if event.id is None:
            return

        summary = approval_summary(event.method, params)

        # Check session-level auto-approval first.
        session_key = self._approvals.session_key(event.method, params)
        session_hit = self._approvals.is_session_approved(room_id, session_key)

        async with AsyncExitStack() as answers:
            if session_hit:
                decision: ApprovalDecision = "acceptForSession"
                decided_by = "session_policy"
            elif self._room_closing(room_id):
                decision = "decline"
                decided_by = "room_cleanup"
            elif self.config.approval_mode == "manual":
                try:
                    decision = await answers.enter_async_context(
                        self._resolve_manual_approval(
                            tools=tools,
                            audience=audience,
                            room_id=room_id,
                            event=event,
                            summary=summary,
                            params=params,
                        )
                    )
                    decided_by = ", ".join(a["name"] for a in audience) or "user"
                except Exception:
                    # Ensure we still answer the server request even if the
                    # human-facing notification flow fails.
                    logger.exception(
                        "Manual approval flow failed; defaulting to decline"
                    )
                    decision = "decline"
                    decided_by = "system_fallback"
            else:
                decision = (
                    "accept"
                    if self.config.approval_mode == "auto_accept"
                    else "decline"
                )
                decided_by = f"policy:{self.config.approval_mode}"

            await client.respond(event.id, approval_response(decision))

        audit_entry = self._approvals.record(
            room_id,
            request_id=str(event.id),
            method=event.method,
            decision=decision,
            decided_by=decided_by,
            summary=summary,
            session_level=session_hit,
        )
        if Emit.TASK_EVENTS in self.features.emit:
            await approval_resolved(tools, room_id, audit_entry)

        if (
            self.config.approval_mode != "manual"
            and self.config.approval_text_notifications
        ):
            room = self._room_clients.get(room_id)
            try:
                mentions = await automatic_mentions(
                    tools, audience_sender_ids(audience)
                )
                if (
                    self._room_clients.get(room_id) is room
                    and not self._room_closing(room_id)
                    and mentions
                ):
                    await tools.send_notice(
                        f"Approval requested ({summary}). Policy decision: {decision}.",
                        mentions=mentions,
                    )
            except Exception:
                logger.exception("Failed to send approval policy notification")

        if Emit.THOUGHTS in self.features.emit:
            # Best-effort telemetry — never fail the turn on thought emission
            # failures.
            await send_event_safe(
                tools,
                content=(f"Codex approval request handled automatically ({decision})."),
                message_type="thought",
                metadata={
                    "codex_approval_method": event.method,
                    "codex_approval_type": approval_type(event.method),
                    "codex_approval_options": list(APPROVAL_OPTIONS),
                },
                log_label="approval thought event",
                log_level=logging.DEBUG,
            )

    async def _emit_turn_outcome(
        self,
        *,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        thread_id: str,
        turn_id: str | None,
        turn_status: str,
        turn_error: str,
        native_messages: dict[str, NativeMessage],
        duration_s: float = 0.0,
        include_reply: bool = True,
    ) -> None:
        # Look up token usage once for both marker and lifecycle events.
        room = self._room_clients.get(room_id)
        usage = self._token_usage.get(thread_id)
        has_usage = usage is not None and usage.total_tokens > 0

        # Emit this turn's usage through the unified Emit.USAGE seam so the
        # toolkit's capture.usage() reads codex like every other adapter. This
        # is independent of the codex-specific emit_token_usage_events path
        # (which embeds codex_*_tokens on the marker/lifecycle task events);
        # emit_usage no-ops unless Emit.USAGE is enabled and usage is non-empty.
        await self.emit_usage(tools, turn_usage(usage))

        if (
            Emit.TASK_EVENTS in self.features.emit
            and self.config.emit_turn_task_markers
        ):
            summary = f"Thread: {thread_id}"
            if turn_error:
                summary += f" | Error: {turn_error}"
            metadata: dict[str, Any] = {
                CodexMetadataKey.ROOM: room_id,
                CodexMetadataKey.THREAD: thread_id,
                CodexMetadataKey.TURN: turn_id,
                "codex_turn_status": turn_status,
                "codex_error": turn_error or None,
            }
            if duration_s > 0:
                metadata["codex_duration_s"] = round(duration_s, 2)
            if has_usage:
                metadata.update(usage.to_metadata())
            await send_event_safe(
                tools,
                content=task_event_content(
                    task_id=turn_id,
                    task="Codex turn",
                    status=turn_status,
                    summary=summary,
                ),
                message_type="task",
                metadata=metadata,
                log_label="turn outcome task event",
                log_level=logging.DEBUG,
            )

        # Phase 2: Enriched turn lifecycle events
        if (
            self.config.emit_turn_lifecycle_events
            and Emit.TASK_EVENTS in self.features.emit
        ):
            lifecycle_metadata: dict[str, Any] = {
                "codex_event_type": "turn_lifecycle",
                CodexMetadataKey.ROOM: room_id,
                CodexMetadataKey.THREAD: thread_id,
                CodexMetadataKey.TURN: turn_id,
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
            await send_event_safe(
                tools,
                content=task_event_content(
                    task_id=turn_id,
                    task="Codex turn lifecycle",
                    status=turn_status,
                    summary=f"Duration: {duration_s:.1f}s | Thread: {thread_id}",
                ),
                message_type="task",
                metadata=lifecycle_metadata,
                log_label="turn lifecycle event",
                log_level=logging.DEBUG,
            )

        if not include_reply:
            return

        if turn_status == CodexTurnStatus.COMPLETED:
            if not tools.turn.replied and Emit.THOUGHTS in self.features.emit:
                for item_id, message in native_messages.items():
                    text = message.unstreamed_text
                    if not has_visible_content(text):
                        continue
                    await send_event_safe(
                        tools,
                        content=text,
                        message_type="thought",
                        metadata={
                            "codex_item_id": item_id,
                            CodexMetadataKey.ROOM: room_id,
                            CodexMetadataKey.THREAD: thread_id,
                            CodexMetadataKey.TURN: turn_id,
                        },
                        log_label="Codex completed thought",
                    )
            if not tools.turn.complete:
                mentions = await automatic_mentions(
                    tools, audience_sender_ids(audience)
                )
                if self._room_clients.get(room_id) is not room or self._room_closing(
                    room_id
                ):
                    return
                await finish_automatic_reply(tools, None, mentions)
            return

        if turn_status == CodexTurnStatus.INTERRUPTED:
            outcome_text = "I stopped before completing this request."
        elif turn_error:
            outcome_text = (
                f"I couldn't complete this request ({turn_status}): {turn_error}"
            )
        else:
            outcome_text = f"I couldn't complete this request ({turn_status})."
        mentions = await automatic_mentions(tools, audience_sender_ids(audience))
        if self._room_clients.get(room_id) is not room or self._room_closing(room_id):
            return
        if mentions:
            await deliver_notice(tools, outcome_text, mentions=mentions)
        tools.turn.settle()

    async def _show_item_started(
        self, tools: AgentToolsProtocol, item: dict[str, Any], token: object
    ) -> None:
        item_type = item.get("type", "")
        if item_type == CodexItemType.CONTEXT_COMPACTION:
            await start_memory_activity(tools, token)
        elif item_type in TOOL_ITEM_TYPES:
            name = tool_item(item_type, item).name
            await start_native_tool_activity(tools, token, name)

    @asynccontextmanager
    async def _resolve_manual_approval(
        self,
        *,
        tools: AgentToolsProtocol,
        audience: list[dict[str, str]],
        room_id: str,
        event: RpcEvent,
        summary: str,
        params: dict[str, Any],
    ) -> AsyncIterator[ApprovalDecision]:
        if event.id is None:
            raise RuntimeError("approval request must have an id")
        loop = asyncio.get_running_loop()
        pending = PendingApproval(
            request_id=event.id,
            method=event.method,
            summary=summary,
            created_at=datetime.now(UTC),
            future=loop.create_future(),
            session_key=self._approvals.session_key(event.method, params),
            native_key=native_approval_key(event.id, params),
        )
        registry = self._approvals.registry(room_id)
        if (registration := self._approvals.register(room_id, pending)) is None:
            logger.warning(
                "Approval %s in room %s was redelivered after a reply claimed it; "
                "declining the redelivery",
                pending.native_key,
                room_id,
            )
            yield "decline"
            return
        entry = registration.entry
        room = self._room_clients.get(room_id)
        token = entry.token
        for removed in registration.removed:
            removed.payload.future.set_result("decline")
        if (evicted := registration.evicted) is not None:
            logger.warning(
                "Evicted oldest pending approval %s in room %s (limit %s)",
                evicted.token,
                room_id,
                self.config.max_pending_approvals_per_room,
            )
        with permission_wait(tools):
            try:
                approval_msg = APPROVAL_REQUESTED_TEMPLATE.format(
                    summary=summary, token=token
                )
                # Emit enriched metadata as a task event for UI rendering
                if Emit.TASK_EVENTS in self.features.emit:
                    approval_metadata: dict[str, Any] = {
                        "codex_event_type": "approval_request",
                        "codex_approval_type": approval_type(event.method),
                        "codex_approval_method": event.method,
                        CodexMetadataKey.ROOM: room_id,
                        "codex_approval_options": list(APPROVAL_OPTIONS),
                    }
                    if params.get("command"):
                        approval_metadata["codex_command"] = params["command"]
                    if params.get("cwd"):
                        approval_metadata["codex_cwd"] = params["cwd"]
                    if params.get("reason"):
                        approval_metadata["codex_reason"] = params["reason"]
                    # Network context (domains, IPs) when available
                    net_ctx = params.get("networkContext") or params.get(
                        "network_context"
                    )
                    if net_ctx:
                        approval_metadata["codex_network_context"] = net_ctx
                    await send_event_safe(
                        tools,
                        content=task_event_content(
                            task_id=token,
                            task="Codex approval request",
                            status="pending",
                            summary=summary,
                        ),
                        message_type="task",
                        metadata=approval_metadata,
                        log_label="approval request task event",
                        log_level=logging.DEBUG,
                    )
                try:
                    mentions = await automatic_mentions(
                        tools, audience_sender_ids(audience)
                    )
                    if (
                        self._room_clients.get(room_id) is room
                        and not self._room_closing(room_id)
                        and mentions
                        and entry in registry.unclaimed()
                    ):
                        await tools.send_notice(approval_msg, mentions=mentions)
                    elif registry.withdraw(entry):
                        yield "decline"
                        return
                except Exception:
                    logger.exception(
                        "Failed to notify room %s about pending approval %s",
                        room_id,
                        token,
                    )
                    # A reply that claimed it meanwhile (its id is in the task
                    # event above) owns the answer; wait for it below.
                    if registry.withdraw(entry):
                        # The room was never notified, so waiting out the full
                        # approval_wait_timeout_s would misreport a Band delivery
                        # hiccup as a human-decision timeout. Re-raise (rather
                        # than returning "decline") so the caller attributes it
                        # to "system_fallback" instead of the human sender.
                        await tools.send_failure(
                            AgentFailure(
                                CODEX_PROVIDER,
                                "Failed to notify the room about a pending approval "
                                "request; defaulting to decline.",
                            )
                        )
                        raise

                self._release_turn(room_id, tools)
                async with registry.answering(
                    entry, pending.future, timeout_s=self.config.approval_wait_timeout_s
                ) as decision:
                    if decision is Timeout.TIMED_OUT:
                        decision = self.config.approval_timeout_decision
                        try:
                            mentions = await automatic_mentions(
                                tools, audience_sender_ids(audience)
                            )
                            if (
                                self._room_clients.get(room_id) is room
                                and not self._room_closing(room_id)
                                and mentions
                            ):
                                await tools.send_notice(
                                    APPROVAL_TIMED_OUT_TEMPLATE.format(
                                        token=token, decision=decision
                                    ),
                                    mentions=mentions,
                                )
                        except Exception:
                            logger.exception(
                                "Failed to send approval timeout notification"
                            )
                    yield decision
            finally:
                self._approvals.forget(room_id, entry)

    def _update_token_usage(self, thread_id: str, params: dict[str, Any]) -> None:
        """Update accumulated token usage for a thread."""
        usage = self._token_usage.get(thread_id)
        if usage is None:
            usage = CodexTokenUsage()
            self._token_usage[thread_id] = usage
        usage.update(params)

    async def _handle_local_command(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        command: CodexCommand,
        args: str,
    ) -> bool:
        reply = await self._local_command_reply(msg, room_id, command, args.strip())
        return await self._answer_command(tools, msg, reply)

    async def _handle_approval_command(
        self,
        *,
        tools: AgentToolsProtocol,
        msg: PlatformMessage,
        room_id: str,
        command: CodexCommand,
        args: str,
    ) -> bool:
        reply = self._approval_command_reply(room_id, command, args.strip())
        return await self._answer_command(tools, msg, reply)

    async def _answer_command(
        self, tools: AgentToolsProtocol, msg: PlatformMessage, reply: str | None
    ) -> bool:
        """Answer a handled command's sender; ``None`` means it was not handled."""
        if reply is None:
            return False
        await deliver_notice(tools, reply, mentions=self._sender_mention(msg))
        return True

    async def _local_command_reply(
        self, msg: PlatformMessage, room_id: str, command: CodexCommand, args: str
    ) -> str | None:
        match command:
            case CodexCommand.HELP:
                return HELP_TEXT
            case CodexCommand.STATUS:
                return self._status_text(room_id)
            case CodexCommand.MODEL | CodexCommand.MODELS:
                return await self._model_reply(args)
            case CodexCommand.REASONING:
                return await self._reasoning_reply(args.lower())
            case CodexCommand.SANDBOX:
                return self._sandbox_reply(msg, room_id, args)
            case CodexCommand.PERMISSIONS:
                return self._permissions_text(room_id)
            case CodexCommand.THREADS | CodexCommand.THREAD:
                return self._thread_reply(room_id, command, args.lower())
            case CodexCommand.USAGE:
                return self._usage_reply(room_id)
            case _:
                return None

    def _usage_summary(self, thread_id: str | None) -> str:
        usage = self._token_usage.get(thread_id) if thread_id else None
        return usage.format_summary() if usage and usage.total_tokens > 0 else "none"

    def _status_text(self, room_id: str) -> str:
        mapped_thread = self._room_threads.get(room_id)
        room = self._active_client_state()
        selected_model = room.selected_model if room is not None else None
        return (
            "Codex status:\n"
            f"- transport: {self.config.transport}\n"
            f"- selected_model: {selected_model or 'unknown'}\n"
            f"- configured_model: {self.config.model or 'auto'}\n"
            f"- room_id: {room_id}\n"
            f"- thread_id: {mapped_thread or 'not mapped'}\n"
            f"- approval_policy: {self.config.approval_policy}\n"
            f"- approval_mode: {self.config.approval_mode}\n"
            f"- sandbox: {self._effective_sandbox(room_id) or 'default'}\n"
            f"- reasoning_effort: {self.config.reasoning_effort or 'default'}\n"
            f"- reasoning_summary: {self.config.reasoning_summary or 'default'}\n"
            f"- pending_approvals: {self._approvals.open_count(room_id)}\n"
            f"- session_approvals: {len(self._approvals.session_patterns(room_id))}\n"
            f"- token_usage: {self._usage_summary(mapped_thread)}\n"
            f"- turn_task_markers: {self.config.emit_turn_task_markers}"
        )

    async def _model_reply(self, model_arg: str) -> str:
        if not model_arg:
            return (
                "Current model: "
                f"`{self._selected_model or 'unknown'}` "
                f"(configured: `{self.config.model or 'auto'}`). "
                f"Use `/{CodexCommand.MODEL} {CodexSubcommand.LIST}` to view available "
                f"models or `/{CodexCommand.MODEL} <id>` to override."
            )
        if model_arg.lower() in MODEL_LIST_WORDS:
            client = self._require_active_client_state().require_client()
            result = await client.request(CodexRequestMethod.MODEL_LIST, {})
            if not (models := visible_model_ids(result)):
                return "No visible models returned by Codex app-server."
            preview = ", ".join(models[:10]) + (", ..." if len(models) > 10 else "")
            return f"Available models ({len(models)}): {preview}"
        # The room's choice outlives its process; never adapter-wide config.
        self._selected_model = model_arg
        return f"Model override set to `{model_arg}` for subsequent turns."

    async def _reasoning_reply(self, effort_arg: str) -> str:
        model_id, efforts = await self._current_model_efforts()
        supported = ", ".join(efforts)
        if not effort_arg:
            hint = f"`{model_id}` supports: {supported}. " if efforts else ""
            return (
                f"Current reasoning effort: `{self.config.reasoning_effort or 'default'}`. "
                f"Summary: `{self.config.reasoning_summary or 'default'}`. "
                f"{hint}Use `/reasoning <effort>` to override."
            )
        # A person's typo would otherwise fail every later turn; the
        # setreasoning tool can't check this because it runs mid-turn.
        if efforts and effort_arg not in efforts:
            return (
                f"`{model_id}` doesn't support reasoning effort `{effort_arg}`. "
                f"Supported: {supported}."
            )
        self._require_active_client_state().reasoning_effort = effort_arg
        return f"Reasoning effort set to `{effort_arg}` for subsequent turns."

    def _sandbox_reply(self, msg: PlatformMessage, room_id: str, mode_arg: str) -> str:
        if self.config.sandbox_policy is not None:
            return (
                "Cannot override sandbox: a `sandbox_policy` is configured. "
                "Remove `sandbox_policy` from config to use per-room "
                f"`/{CodexCommand.SANDBOX}` overrides."
            )
        if not mode_arg:
            effective = self._effective_sandbox(room_id) or "default"
            return (
                f"Current sandbox: `{effective}`. "
                f"Use `/{CodexCommand.SANDBOX} <{SANDBOX_MODE_CHOICES}>` to change."
            )
        # Parse tokens explicitly so "--confirm-anything-else" is surfaced
        # as an unknown mode instead of silently stripped away.
        tokens = mode_arg.split()
        confirmed = SANDBOX_CONFIRM_FLAG in tokens
        mode_tokens = [tok for tok in tokens if tok != SANDBOX_CONFIRM_FLAG]
        if len(mode_tokens) != 1:
            return (
                f"Usage: `/{CodexCommand.SANDBOX} <{SANDBOX_MODE_CHOICES}> "
                f"[{SANDBOX_CONFIRM_FLAG}]`."
            )
        mode_token = mode_tokens[0]
        if (normalized := normalize_sandbox_mode(mode_token)) is None:
            return (
                f"Invalid sandbox mode `{mode_token}`. "
                f"Valid: {', '.join(CodexSandboxMode)}."
            )
        if normalized == CodexSandboxMode.DANGER_FULL_ACCESS:
            if not confirmed:
                return (
                    f"Escalating to `{normalized}` removes all sandbox "
                    f"restrictions. Re-run with `{SANDBOX_CONFIRM_FLAG}` to proceed:\n"
                    f"`/{CodexCommand.SANDBOX} {normalized} {SANDBOX_CONFIRM_FLAG}`"
                )
            logger.warning(
                "Sandbox escalated to %s via /%s command in room %s by %s",
                normalized,
                CodexCommand.SANDBOX,
                room_id,
                msg.sender_name or msg.sender_type or "unknown",
            )
        self._sandbox_overrides[room_id] = normalized
        return f"Sandbox mode set to `{normalized}` for subsequent turns in this room."

    def _permissions_text(self, room_id: str) -> str:
        session_approved = self._approvals.session_patterns(room_id)
        audit = self._approvals.audit(room_id)
        lines = [
            "Effective permissions:",
            f"- approval_mode: {self.config.approval_mode}",
            f"- sandbox: {self._effective_sandbox(room_id) or 'default'}",
            f"- approval_policy: {self.config.approval_policy}",
        ]
        if session_approved:
            lines.append(f"- session_approved patterns: {len(session_approved)}")
            lines.extend(f"  - {pattern}" for pattern in session_approved)
        if audit:
            lines.append(f"- approval_history: {len(audit)} decisions")
            lines.extend(
                f"  - [{entry.timestamp}] {entry.method}: "
                f"{entry.decision} by {entry.decided_by}"
                for entry in audit[-5:]
            )
        return "\n".join(lines)

    def _thread_reply(
        self, room_id: str, command: CodexCommand, subcommand: str
    ) -> str | None:
        if command == CodexCommand.THREADS or not subcommand:
            if not self._room_threads:
                return "No active thread mappings."
            lines = ["Active thread mappings:"]
            lines.extend(
                f"- room `{rid}` → thread `{tid}`"
                + (" (current)" if rid == room_id else "")
                for rid, tid in self._room_threads.items()
            )
            return "\n".join(lines)
        match subcommand:
            case CodexSubcommand.INFO:
                if not (mapped_thread := self._room_threads.get(room_id)):
                    return "No thread mapped for this room."
                return (
                    f"Thread info:\n"
                    f"- thread_id: {mapped_thread}\n"
                    f"- room_id: {room_id}\n"
                    f"- token_usage: {self._usage_summary(mapped_thread)}"
                )
            case CodexSubcommand.ARCHIVE:
                mapped_thread = self._forget_room_thread(room_id)
                self._require_active_client_state().forget_thread()
                return (
                    f"Thread `{mapped_thread or 'none'}` archived. "
                    "A new thread will be created on next message."
                )
            case _:
                return None

    def _usage_reply(self, room_id: str) -> str:
        if not (mapped_thread := self._room_threads.get(room_id)):
            return "No thread mapped for this room — no usage data."
        usage = self._token_usage.get(mapped_thread)
        if not usage or usage.total_tokens == 0:
            return "No token usage recorded for this thread."
        return f"Thread `{mapped_thread}` — {usage.format_summary()}"

    def _approval_command_reply(
        self, room_id: str, command: CodexCommand, args: str
    ) -> str | None:
        pending = self._approvals.pending(room_id)
        if command == CodexCommand.APPROVALS:
            if not (open_entries := pending.unclaimed()):
                return "No pending approvals."
            now = datetime.now(UTC)
            lines = ["Pending approvals:"]
            lines.extend(
                f"- {entry.token}: {entry.payload.summary} "
                f"({int((now - entry.payload.created_at).total_seconds())}s)"
                for entry in open_entries
            )
            return "\n".join(lines)

        if (decision := APPROVAL_COMMAND_DECISIONS.get(command)) is None:
            return None
        if not pending:
            return NO_APPROVALS_TO_RESOLVE_MESSAGE

        open_ids = sorted(entry.token for entry in pending.unclaimed())
        token = args.split(" ", 1)[0] if args else ""
        if not token:
            match open_ids:
                case []:
                    return NO_APPROVALS_TO_RESOLVE_MESSAGE
                case [only]:
                    token = only
                case _:
                    return (
                        "Multiple approvals pending. "
                        f"Use `/{command} <id>`. Pending: {', '.join(open_ids)}"
                    )

        if (selected := pending.get(token)) is None:
            return (
                f"Unknown approval id `{token}`. "
                f"Pending: {', '.join(open_ids) or 'none'}"
            )

        for_session = command == CodexCommand.APPROVE_SESSION
        # A session approval needs a key (e.g. a concrete command) that later
        # requests can match; an empty one would match nothing and mislead.
        if for_session and not selected.session_key:
            return (
                f"Approval `{token}` cannot be resolved as session-level: "
                "this request has no command signature to match against. "
                f"Use `/{CodexCommand.APPROVE} {token}` for a one-shot approval instead."
            )
        if pending.try_claim(token) is None:
            return APPROVAL_NOT_PENDING_TEMPLATE.format(token=token)

        selected.future.set_result(decision)
        resolved = APPROVAL_RESOLVED_TEMPLATE.format(token=token, decision=decision)
        if not for_session:
            return resolved
        self._approvals.approve_for_session(room_id, selected.session_key)
        return (
            resolved + " This session-level approval auto-approves future "
            f"`{selected.session_key}` requests."
        )

    def _build_system_prompt(self) -> None:
        if self.config.system_prompt:
            self._system_prompt = (
                f"{self.config.system_prompt}\n\n{COMMUNICATION_INSTRUCTIONS}"
            )
            return

        self._system_prompt = render_system_prompt(
            agent_name=self.agent_name or "Agent",
            agent_description=self.agent_description or "An AI assistant",
            custom_section=self.config.custom_section,
            include_base_instructions=self.config.include_base_instructions,
            features=self.features,
        )
        if not self.config.include_base_instructions:
            self._system_prompt += f"\n\n{COMMUNICATION_INSTRUCTIONS}"

    def _apply_turn_overrides(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        state = self._require_active_client_state()
        params["model"] = self._selected_model
        params["cwd"] = state.workspace
        params["approvalPolicy"] = self.config.approval_policy
        params["personality"] = self.config.personality
        if state.reasoning_effort or self.config.reasoning_effort:
            params["effort"] = state.reasoning_effort or self.config.reasoning_effort
        if state.reasoning_summary or self.config.reasoning_summary:
            params["summary"] = state.reasoning_summary or self.config.reasoning_summary
        apply_turn_sandbox(
            params,
            policy=self.config.sandbox_policy,
            effective=self._effective_sandbox(room_id),
        )

    def _apply_thread_sandbox(
        self, params: dict[str, Any], *, room_id: str | None = None
    ) -> None:
        apply_thread_sandbox(
            params,
            policy=self.config.sandbox_policy,
            windows_sandbox=self.config.windows_sandbox,
            effective=self._effective_sandbox(room_id),
        )

    def _effective_sandbox(self, room_id: str | None = None) -> str | None:
        """Return the effective sandbox for *room_id*, checking per-room overrides first."""
        if room_id:
            override = self._sandbox_overrides.get(room_id)
            if override is not None:
                return override
        return self.config.sandbox

    @staticmethod
    def _sender_mention(msg: PlatformMessage) -> list[dict[str, str]]:
        return [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]

    @staticmethod
    def _turn_audience(messages: Sequence[PlatformMessage]) -> list[dict[str, str]]:
        """The automatic notices' recipients: one mention per distinct sender."""
        names = {m.sender_id: m.sender_name or m.sender_type for m in messages}
        return [
            {"id": sender_id, "name": names[sender_id]}
            for sender_id in distinct_sender_ids(messages)
        ]

    def _clear_pending_approvals_for_room(self, room_id: str) -> None:
        """ApprovalInterruptMixin's hook: an interrupt declines the room's approvals."""
        self._approvals.decline_all(room_id)

    async def _current_model_efforts(self) -> tuple[str, list[str]]:
        """The room's model and the efforts Codex reports for it (empty if unknown)."""
        room = self._require_active_client_state()
        client = room.require_client()
        model_id = room.selected_model or await self._select_model(client)
        try:
            result = await client.request(CodexRequestMethod.MODEL_LIST, {})
        except Exception:
            logger.warning(
                "model/list failed; supported reasoning efforts are unknown",
                exc_info=True,
            )
            return model_id, []
        return model_id, supported_efforts(result, model_id)
