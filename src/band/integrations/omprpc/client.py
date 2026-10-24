"""Persistent subprocess transport for OMP's native JSONL RPC protocol."""

from __future__ import annotations

import asyncio
import logging
import os
from binascii import Error as Base64Error
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from pydantic import JsonValue, ValidationError

from band.integrations.codex.winspawn import (
    CreateProcess,
    SpawnFailure,
    WindowsProcess,
    await_returncode,
    close_stdin,
    kill_process_tree,
)
from band.integrations.omprpc.errors import (
    CALLBACKS_UNFINISHED,
    OmpCompletionTimeout,
    OmpProcessError,
    OmpPromptError,
    OmpProtocolError,
    OmpRpcError,
    command_error,
)
from band.integrations.omprpc.frames import (
    ChunkAssembler,
    parse_payload_line,
    payload_json,
    ui_response_frame,
)
from band.integrations.omprpc.prompt import OmpCallbackKind, PromptRun, idle_flags
from band.integrations.omprpc.scope import OmpProcessScope
from band.integrations.ompwire import (
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OMP_REASSEMBLED_BYTES,
    OmpCommand,
    OmpCommandName,
    OmpEvent,
    OmpEventType,
    OmpFrame,
    OmpFrameError,
    OmpFrameType,
    OmpHostToolCall,
    OmpHostToolCancel,
    OmpHostToolResult,
    OmpNegotiation,
    OmpPromptAcceptance,
    OmpPromptResult,
    OmpPromptStatus,
    OmpReady,
    OmpResponse,
    OmpState,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
    OmpWireModel,
)

TModel = TypeVar("TModel", bound=OmpWireModel)
CloseProcess = Callable[[], Coroutine[Any, Any, None]]
JoinClose = Callable[[Awaitable[None]], Awaitable[None]]

logger = logging.getLogger(__name__)


def _noop() -> None:
    return None


_DRAIN_BYTES = 64 * 1024
_ABORT_TIMEOUT_S = 1.0
PROCESS_EXIT_TIMEOUT_S = 1.0
CALLBACK_EXIT_TIMEOUT_S = 1.0
COMPLETION_TIMEOUT_S = 30.0
_EVENT_TYPES = frozenset(OmpEventType)
_UI_NOTIFICATIONS = frozenset(
    {"notify", "setStatus", "setWidget", "setTitle", "set_editor_text", "open_url"}
)


@dataclass(frozen=True)
class ReceivedResponse:
    frame: OmpResponse
    sequence: int


@dataclass(frozen=True)
class PendingRequest:
    command: str
    future: asyncio.Future[ReceivedResponse | OmpRpcError | OmpProcessError]


class OmpRpcClient:
    """One OMP process, one persistent reader, and at most one active prompt."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        create_process: CreateProcess | None = None,
    ) -> None:
        if not command:
            raise ValueError("OMP command must not be empty")
        self.command = tuple(command)
        self.env = None if env is None else {**os.environ, **env}
        self.create_process = create_process
        self.close_process: CloseProcess = self._close_process
        self.join_close: JoinClose = asyncio.shield
        self.after_stdin_closed: Callable[[], None] = _noop
        self.callback_exit_timeout_s = CALLBACK_EXIT_TIMEOUT_S
        self.process_exit_timeout_s = PROCESS_EXIT_TIMEOUT_S
        self.completion_timeout_s = COMPLETION_TIMEOUT_S
        self._proc: asyncio.subprocess.Process | None = None
        self._windows_process: WindowsProcess | None = None
        self._process_scope: OmpProcessScope | None = None
        self._spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[OmpReady | OmpProcessError] | None = None
        self._failure: OmpProcessError | None = None
        self._closing = False
        self._request_id = 0
        self._sequence = 0
        self._frames = ChunkAssembler()
        self._max_frame_bytes = OMP_FRAME_BYTES
        self._pending: dict[str, PendingRequest] = {}
        self._turn: PromptRun | None = None
        self._retirement_error: OmpProcessError | None = None
        self._survivors: set[asyncio.Task[None]] = set()
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._prompt_lock = asyncio.Lock()
        self._write_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Start the supplied command and require the native RPC protocol."""
        async with self._start_lock:
            if self._retirement_error is not None:
                raise self._retirement_error
            if self._closing:
                await self.close()
                self._proc = None
                self._spawn_task = None
                self._close_task = None
            if self._proc is not None:
                self._ensure_running()
                return
            if self._failure is not None:
                logger.error(
                    "Discarding previous OMP RPC failure on restart: %s", self._failure
                )
            self._failure = None
            self._frames.reset()
            self._frames.max_reassembled_bytes = OMP_REASSEMBLED_BYTES
            self._max_frame_bytes = OMP_FRAME_BYTES
            self._closing = False
            self._process_scope = OmpProcessScope()
            self._ready = asyncio.get_running_loop().create_future()
            self._spawn_task = asyncio.create_task(self._spawn_process())
            try:
                process = await asyncio.shield(self._spawn_task)
                if self._closing:
                    raise OmpProcessError("OMP RPC client closed during startup")
                self._proc = process
                self._reader_task = asyncio.create_task(self._read_stdout(self._proc))
                self._stderr_task = asyncio.create_task(self._read_stderr(self._proc))
                ready = await asyncio.shield(self._ready)
                if isinstance(ready, OmpProcessError):
                    raise ready
                if OMP_NATIVE_PROTOCOL_VERSION not in ready.supportedProtocolVersions:
                    error = OmpProtocolError(
                        f"OMP RPC protocol v{OMP_NATIVE_PROTOCOL_VERSION} is required"
                    )
                    self._record_failure(error)
                    raise error
                self._apply_ready_limits(ready)
                negotiated = await self._request_frame(
                    OmpCommandName.NEGOTIATE_PROTOCOL,
                    {"protocolVersion": OMP_NATIVE_PROTOCOL_VERSION},
                )
                self._parse_response(
                    negotiated.frame,
                    OmpNegotiation,
                    f"OMP RPC protocol v{OMP_NATIVE_PROTOCOL_VERSION} negotiation failed",
                )
            except (OSError, SpawnFailure) as cause:
                await self.close()
                raise OmpProcessError("Unable to start OMP RPC process") from cause
            except (Exception, asyncio.CancelledError):
                await self.close()
                raise

    async def spawn_owned_process(
        self, *command: str, **kwargs: Any
    ) -> asyncio.subprocess.Process:
        """Start and retain the command within this client's process containment.

        ``create_process`` is the low-level launcher, including for the Windows
        helper. Wrappers override this method and delegate to its bound original
        or ``super()`` so creation owns exactly one helper and Job.
        """
        if os.name == "nt":
            self._windows_process = WindowsProcess(create_process=self.create_process)
            process = await self._windows_process.start_command(
                command,
                cwd=cast(str | None, kwargs.get("cwd")),
                env=cast(Mapping[str, str] | None, kwargs.get("env")),
                limit=cast(int, kwargs.get("limit", OMP_FRAME_BYTES)),
            )
        else:
            create = self.create_process or asyncio.create_subprocess_exec
            process = await create(*command, **kwargs)
        # A wrapper may hold or fail after creation; cleanup still owns the child.
        self._proc = process
        return process

    async def _spawn_process(self) -> asyncio.subprocess.Process:
        scope = self._process_scope
        assert scope is not None
        env = scope.environment(self.env)
        return await self.spawn_owned_process(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=None,
            env=env,
            start_new_session=os.name != "nt",
            limit=OMP_FRAME_BYTES,
        )

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        """Correlate a native command response without consuming prompt events."""
        self._ensure_running()
        response = await self._request_frame(command, parameters)
        return self._response_data(response.frame)

    async def prompt(
        self,
        message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        """Deliver a turn through its correlated result and actual native settlement."""
        async with self._prompt_lock:
            self._ensure_running()
            await self._execute_prompt(message, on_event, on_tool, on_ui)

    def stop_after_tool(self, call_id: str) -> None:
        """Stop this prompt after acknowledging the successful host tool call."""
        turn = self._turn
        if turn is None or (OmpCallbackKind.TOOL, call_id) not in turn.callbacks:
            raise ValueError("OMP stop requires an active host tool callback")
        if turn.stop_after_tool_id is None:
            turn.stop_after_tool_id = call_id

    def _retire_survivors(self) -> OmpProcessError | None:
        """Re-prove callback retirement on every repeat close attempt."""
        pending = {task for task in self._survivors if not task.done()}
        if pending:
            if self._retirement_error is None:
                self._retirement_error = OmpProcessError(
                    f"OMP callbacks survived cancellation: {len(pending)} task(s)"
                    " still pending; retirement is unproven"
                )
            return self._retirement_error
        if self._survivors:
            # Every survivor has finished: callback cleanup is now proven and
            # replacement is unblocked.
            for task in self._survivors:
                OmpRpcClient._consume_task(task)
            self._retirement_error = None
        self._survivors.clear()
        return None

    async def close(self) -> None:
        """Finish shared bounded cleanup before propagating caller cancellation."""
        if self._proc is None and self._spawn_task is None:
            error = self._retire_survivors()
            if error is not None:
                raise error
            return
        self._begin_close()
        closing = self._close_task
        assert closing is not None
        interrupted = False
        while True:
            try:
                await self.join_close(closing)
                break
            except asyncio.CancelledError:
                if closing.cancelled():
                    raise
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError

    def _ensure_running(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closing or self._proc is None:
            raise OmpProcessError("OMP RPC process is not running")
        if self._proc.returncode is not None:
            error = OmpProcessError("OMP RPC process exited")
            self._record_failure(error)
            raise error

    def _next_id(self) -> str:
        self._request_id += 1
        return f"band-{self._request_id}"

    async def _request_frame(
        self,
        command: str,
        parameters: Mapping[str, JsonValue],
        *,
        request_id: str | None = None,
        allow_closing: bool = False,
        preceding_frame: dict[str, JsonValue] | None = None,
        completion: bool = False,
    ) -> ReceivedResponse:
        if not allow_closing:
            self._ensure_running()
        if "id" in parameters or "type" in parameters:
            raise ValueError("OMP command parameters must not override id or type")
        request_id = request_id or self._next_id()
        frame = OmpCommand.model_validate(
            {"type": str(command), "id": request_id, **parameters}
        )
        future: asyncio.Future[ReceivedResponse | OmpRpcError | OmpProcessError] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = PendingRequest(command, future)
        try:
            if preceding_frame is None:
                await self._write_frame(frame.root)
            else:
                await self._write_frames(preceding_frame, frame.root)
            if completion:
                try:
                    async with asyncio.timeout(self.completion_timeout_s):
                        response = await future
                except TimeoutError as cause:
                    raise OmpCompletionTimeout(
                        f"OMP {command} response timed out during prompt completion;"
                        " the turn outcome is uncertain"
                    ) from cause
            else:
                response = await future
            if isinstance(response, (OmpRpcError, OmpProcessError)):
                raise response
            return response
        finally:
            self._pending.pop(request_id, None)

    def _response_data(self, response: OmpResponse) -> dict[str, JsonValue]:
        if response.data is None:
            return {}
        if not isinstance(response.data, dict):
            error = OmpProtocolError("OMP command response data must be an object")
            self._record_failure(error)
            raise error
        return response.data

    def _parse_response(
        self, response: OmpResponse, model: type[TModel], error: str
    ) -> TModel:
        try:
            return model.model_validate(self._response_data(response))
        except ValidationError as cause:
            failure = OmpProtocolError(error)
            failure.__cause__ = cause
            self._record_failure(failure)
            raise failure from cause

    async def _execute_prompt(
        self,
        message: str,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        turn = PromptRun(self._next_id(), on_event, on_tool, on_ui)
        self._turn = turn
        event_worker = asyncio.create_task(self._deliver_events(turn))
        turn.event_worker = event_worker
        work = asyncio.create_task(self._complete_prompt(turn, message))
        guard = asyncio.create_task(self._guard_completion(turn))
        try:
            done, _ = await asyncio.wait(
                (work, turn.failure), return_when=asyncio.FIRST_COMPLETED
            )
            if turn.failure in done:
                raise turn.failure.result()
            await work
        except OmpRpcError:
            if not turn.rejected:
                await self._close_preserving()
            raise
        except OmpPromptError:
            raise
        except BaseException:
            await self._close_preserving()
            raise
        finally:
            workers = [work, event_worker, guard, *turn.callback_tasks]
            if turn.stop_task is not None:
                workers.append(turn.stop_task)
            self._survivors.update(turn.callback_tasks)
            self._survivors.add(event_worker)
            self._turn = None
            try:
                await self._cancel_tasks(
                    workers,
                    timeout=self.callback_exit_timeout_s,
                    label="callback",
                )
            finally:
                if error := self._retire_survivors():
                    self._record_failure(error)

    async def _close_preserving(self) -> None:
        # A retirement failure must not replace the honest completion failure;
        # it stays recorded so retirement-dependent restarts fail closed.
        try:
            await self.close()
        except asyncio.CancelledError:
            raise
        except Exception as cleanup:  # noqa: BLE001 -- preserve the turn error
            logger.error("OMP retirement failed after prompt error: %s", cleanup)

    async def _guard_completion(self, turn: PromptRun) -> None:
        """Arm the completion budget only on positively proven idle evidence.

        Any newer activity or busy evidence invalidates and stops an armed
        timer; re-arming requires a new eligible observation. The guard never
        synthesizes a result or marks work complete: on expiry it fails the
        turn with the typed uncertain-outcome error, which unwinds through the
        same retirement path as any transport failure.
        """
        armed_since: float | None = None
        try:
            while True:
                # Clear before evaluating so any change observed afterwards
                # is visible to the next wait; evaluation then always sees
                # the freshest state at the moment the wait is armed.
                turn.changed.clear()
                if self._turn is not turn or turn.rejected:
                    return
                if turn.completion_evidence_complete():
                    return
                if turn.result_owed and not turn.boundary_reached():
                    armed_since = None
                    await turn.changed.wait()
                    continue
                if turn.busy_proven():
                    armed_since = None
                    await turn.changed.wait()
                    continue
                stale = (
                    turn.snapshot is None
                    or turn.snapshot[0] <= turn.last_activity_sequence
                    or turn.snapshot[0] < turn.settled_sequence
                )
                if stale:
                    received = await self._request_frame(
                        OmpCommandName.GET_STATE, {}, completion=True
                    )
                    turn.record_snapshot(
                        received.sequence,
                        self._parse_response(
                            received.frame,
                            OmpState,
                            "Invalid OMP completion protocol data",
                        ),
                    )
                    continue
                now = asyncio.get_running_loop().time()
                if armed_since is None:
                    armed_since = now
                    continue
                remaining = self.completion_timeout_s - (now - armed_since)
                if remaining <= 0:
                    turn.fail(
                        OmpCompletionTimeout(
                            "OMP completion stayed idle without its result,"
                            " settlement, or final drain; the turn outcome is"
                            " uncertain"
                        )
                    )
                    return
                try:
                    await asyncio.wait_for(turn.changed.wait(), remaining)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- belongs to the awaiting prompt
            turn.fail(error)

    async def _complete_prompt(self, turn: PromptRun, message: str) -> None:
        try:
            accepted = await self._request_frame(
                OmpCommandName.PROMPT, {"message": message}, request_id=turn.id
            )
        except OmpRpcError:
            turn.rejected = True
            raise
        acceptance = self._parse_response(
            accepted.frame,
            OmpPromptAcceptance,
            "Invalid OMP prompt acceptance protocol data",
        )
        turn.result_owed = acceptance.agentInvoked is not False
        turn.changed.set()
        outcome: OmpPromptResult | None = None
        # A synchronous local command explicitly owes no prompt_result.
        if turn.result_owed:
            outcome = await turn.result
        await self._wait_for_settlement(turn)
        await turn.events.join()
        if turn.callback_tasks:
            try:
                async with asyncio.timeout(self.callback_exit_timeout_s):
                    while any(not task.done() for task in turn.callback_tasks):
                        await asyncio.wait(turn.callback_tasks)
            except TimeoutError:
                error = OmpCompletionTimeout(
                    f"{CALLBACKS_UNFINISHED}; the turn outcome is uncertain"
                )
                self._record_failure(error)
                raise error from None
        if turn.stop_task is not None:
            await asyncio.shield(turn.stop_task)
        successful_statuses = {OmpPromptStatus.COMPLETED}
        if turn.stopped:
            successful_statuses.add(OmpPromptStatus.ABORTED)
        if outcome is not None and outcome.status not in successful_statuses:
            raise OmpPromptError(
                outcome.status,
                outcome.error.message
                if outcome.error is not None
                else f"OMP prompt {outcome.status}",
            )
        assistant = turn.last_assistant
        if assistant is not None and (
            assistant.stopReason == OmpPromptStatus.ERROR
            or (assistant.stopReason == OmpPromptStatus.ABORTED and not turn.stopped)
        ):
            raise OmpPromptError(
                assistant.stopReason,
                assistant.errorMessage or f"OMP continuation {assistant.stopReason}",
            )

    async def _wait_for_settlement(self, turn: PromptRun) -> None:
        while True:
            snapshot = await self._request_frame(
                OmpCommandName.GET_STATE, {}, completion=True
            )
            state = self._parse_response(
                snapshot.frame, OmpState, "Invalid OMP settlement protocol data"
            )
            turn.record_snapshot(snapshot.sequence, state)
            # A snapshot proving settlement AND positive idleness, newer than
            # every observed event, proves native completion. isSettled alone
            # must not end a turn: background work can outlive the answer, so
            # the success path demands the same positive-idle conjunction the
            # completion guard requires before arming. A session_settled frame
            # postdating the snapshot and all activity proves it too; an old
            # one cannot overrule a newer busy snapshot, so each wake
            # re-snapshots.
            if (
                state.isSettled
                and idle_flags(state)
                and snapshot.sequence >= turn.last_activity_sequence
            ) or (
                turn.settled_sequence > snapshot.sequence
                and turn.settled_sequence > turn.last_activity_sequence
            ):
                return
            turn.changed.clear()
            await turn.changed.wait()

    async def _deliver_events(self, turn: PromptRun) -> None:
        try:
            while self._turn is turn and not self._closing:
                event = await turn.events.get()
                try:
                    if self._turn is not turn or self._closing:
                        return
                    await turn.on_event(event)
                finally:
                    turn.events.task_done()
                    turn.events_pending -= 1
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- arbitrary callback failures belong to the awaiting prompt
            turn.fail(error)

    async def _write_line(self, line: str) -> None:
        async with self._write_lock:
            process = self._proc
            if process is None or process.stdin is None:
                raise OmpProcessError("OMP RPC stdin is unavailable")
            try:
                process.stdin.write((line + "\n").encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError, OSError) as cause:
                error = OmpProcessError("OMP RPC stdin closed")
                error.__cause__ = cause
                self._record_failure(error)
                raise error from cause

    async def _write_frame(self, frame: dict[str, JsonValue]) -> None:
        await self._write_frames(frame)

    async def _write_frames(self, *frames: dict[str, JsonValue]) -> None:
        await self._write_line("\n".join(payload_json(frame) for frame in frames))

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    if self._closing:
                        return
                    if self._frames.pending:
                        raise OmpProtocolError(
                            "OMP RPC protocol chunks interrupted by stdout EOF"
                        )
                    raise OmpProcessError("OMP RPC stdout EOF")
                if len(line) > self._max_frame_bytes or not line.endswith(b"\n"):
                    raise OmpProtocolError("Invalid OMP RPC protocol frame length")
                payload = self._frames.decode(parse_payload_line(line))
                if payload is None:
                    continue
                self._sequence += 1
                await self._dispatch_payload(payload, self._sequence)
        except asyncio.CancelledError:
            raise
        except OmpProcessError as error:
            self._record_failure(error)
        except (ValidationError, ValueError, Base64Error) as cause:
            error = OmpProtocolError("Invalid OMP RPC protocol frame")
            error.__cause__ = cause
            self._record_failure(error)
        except Exception as cause:  # noqa: BLE001 -- reader failure must unblock every pending request
            error = OmpProcessError("OMP RPC stdout reader failed")
            error.__cause__ = cause
            self._record_failure(error)

    async def _dispatch_payload(
        self, payload: dict[str, JsonValue], sequence: int
    ) -> None:
        frame = OmpFrame.model_validate(payload)
        turn = self._turn
        match frame.type:
            case OmpFrameType.READY:
                ready = OmpReady.model_validate(payload)
                self._apply_ready_limits(ready)
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(ready)
                else:
                    logger.debug("OMP RPC ignored a duplicate ready frame")
            case OmpFrameType.FRAME_ERROR:
                overflow = OmpFrameError.model_validate(payload)
                raise OmpProtocolError(
                    f"OMP RPC protocol frame overflow: {overflow.error}"
                )
            case OmpFrameType.RESPONSE:
                self._dispatch_response(OmpResponse.model_validate(payload), sequence)
            case OmpFrameType.PROMPT_RESULT:
                result = OmpPromptResult.model_validate(payload)
                if turn is not None and result.id == turn.id and not turn.result.done():
                    turn.result.set_result(result)
                    turn.changed.set()
                else:
                    logger.debug("OMP RPC ignored an unmatched prompt_result")
            case OmpFrameType.SESSION_SETTLED:
                if turn is not None and (
                    turn.last_activity_sequence > 0 or turn.result.done()
                ):
                    turn.settled_sequence = sequence
                    turn.changed.set()
            case OmpFrameType.HOST_TOOL_CALL:
                call = OmpHostToolCall.model_validate(payload)
                if turn is None:
                    raise OmpProtocolError(
                        "OMP host tool arrived without an active prompt"
                    )
                self._start_callback(
                    turn, OmpCallbackKind.TOOL, call.id, self._handle_tool(turn, call)
                )
            case OmpFrameType.HOST_TOOL_CANCEL:
                cancellation = OmpHostToolCancel.model_validate(payload)
                if turn is not None:
                    self._cancel_callback(
                        turn, OmpCallbackKind.TOOL, cancellation.targetId
                    )
                else:
                    logger.debug(
                        "OMP RPC ignored a host-tool cancellation without an active prompt"
                    )
            case OmpFrameType.UI_REQUEST:
                request = OmpUIRequest.model_validate(payload)
                if request.method == "cancel":
                    if request.targetId is None:
                        raise OmpProtocolError(
                            "OMP UI cancellation is missing targetId"
                        )
                    if turn is not None:
                        self._cancel_callback(
                            turn, OmpCallbackKind.UI, request.targetId
                        )
                    return
                if request.method in _UI_NOTIFICATIONS:
                    return
                if turn is None:
                    write = asyncio.create_task(
                        self._write_frame(
                            ui_response_frame(request.id, OmpUIResponse(cancelled=True))
                        )
                    )
                    self._write_tasks.add(write)
                    write.add_done_callback(self._write_tasks.discard)
                    write.add_done_callback(self._consume_task)
                    return
                self._start_callback(
                    turn, OmpCallbackKind.UI, request.id, self._handle_ui(turn, request)
                )
            case event_type if event_type in _EVENT_TYPES:
                event = OmpEvent.model_validate(payload)
                if turn is not None:
                    if overflow := turn.observe(event, sequence):
                        self._record_failure(overflow)
                else:
                    logger.debug("OMP RPC ignored an event without an active prompt")
            case _:
                logger.debug("OMP RPC ignored a frame with an unknown type")

    def _dispatch_response(self, response: OmpResponse, sequence: int) -> None:
        if response.id is None:
            raise OmpProtocolError("OMP response is missing its command id")
        pending = self._pending.get(response.id)
        turn = self._turn
        active_prompt = turn if turn is not None and response.id == turn.id else None
        expected_command = pending.command if pending is not None else None
        if expected_command is None and active_prompt is not None:
            expected_command = OmpCommandName.PROMPT
        if expected_command is not None and response.command != expected_command:
            raise OmpProtocolError("OMP response command does not match its request")
        if pending is None:
            if not response.success and active_prompt is not None:
                active_prompt.rejected = True
                active_prompt.fail(command_error(response))
                return
            logger.debug("OMP RPC ignored an unmatched command response")
            return
        if pending.future.done():
            if (
                not response.success
                and self._turn is not None
                and response.id == self._turn.id
            ):
                self._turn.rejected = True
                self._turn.fail(command_error(response))
            return
        if response.success:
            pending.future.set_result(ReceivedResponse(response, sequence))
        else:
            pending.future.set_result(command_error(response))

    def _start_callback(
        self,
        turn: PromptRun,
        kind: OmpCallbackKind,
        request_id: str,
        callback: Coroutine[object, object, None],
    ) -> None:
        if self._closing or turn.failure.done() or turn.stop_after_tool_id is not None:
            callback.close()
            return
        key = (kind, request_id)
        task = asyncio.create_task(callback, context=turn.context.copy())
        if key in turn.callbacks:
            task.cancel()
            raise OmpProtocolError("OMP callback request id was reused")
        turn.callbacks[key] = task
        turn.callback_tasks.add(task)
        task.add_done_callback(lambda done: self._callback_finished(turn, key, done))

    def _callback_still_active(
        self, turn: PromptRun, kind: OmpCallbackKind, request_id: str
    ) -> bool:
        return (
            not self._closing
            and turn.callbacks.get((kind, request_id)) is asyncio.current_task()
        )

    @staticmethod
    def _cancel_callback(
        turn: PromptRun, kind: OmpCallbackKind, request_id: str
    ) -> None:
        task = turn.callbacks.pop((kind, request_id), None)
        if task is not None:
            task.cancel()

    @staticmethod
    def _callback_finished(
        turn: PromptRun, key: tuple[OmpCallbackKind, str], task: asyncio.Task[None]
    ) -> None:
        if turn.callbacks.get(key) is task:
            turn.callbacks.pop(key)
        turn.callback_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            turn.fail(error)

    async def _handle_tool(self, turn: PromptRun, call: OmpHostToolCall) -> None:
        if turn.stop_after_tool_id is not None:
            return
        result = await turn.on_tool(call)
        if turn.stop_after_tool_id not in (None, call.id):
            return
        if not self._callback_still_active(turn, OmpCallbackKind.TOOL, call.id):
            return
        frame = OmpHostToolResult.from_tool(call.id, result).model_dump(mode="json")
        if turn.stop_after_tool_id == call.id:
            turn.stop_task = asyncio.create_task(self._stop_after_result(turn, frame))
        else:
            await self._write_frame(frame)

    async def _stop_after_result(
        self, turn: PromptRun, result: dict[str, JsonValue]
    ) -> None:
        # The bridge must consume the result before abort cancels pending tools.
        try:
            await self._request_frame(OmpCommandName.ABORT, {}, preceding_frame=result)
        except (OmpProcessError, OmpRpcError) as error:
            turn.fail(error)
            return
        turn.stopped = True

    async def _handle_ui(self, turn: PromptRun, request: OmpUIRequest) -> None:
        if turn.stop_after_tool_id is not None:
            return
        response = await turn.on_ui(request)
        if not self._callback_still_active(turn, OmpCallbackKind.UI, request.id):
            return
        await self._write_frame(ui_response_frame(request.id, response))

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        try:
            while data := await process.stderr.read(_DRAIN_BYTES):
                logger.debug("OMP stderr drained: %s bytes", len(data))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("OMP stderr reader failed")

    def _fail_pending(self, error: OmpProcessError) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_result(error)
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(error)
        if self._turn is not None:
            self._turn.fail(error)

    def _apply_ready_limits(self, ready: OmpReady) -> None:
        self._max_frame_bytes = ready.maxFrameBytes
        self._frames.max_reassembled_bytes = ready.maxReassembledFrameBytes

    def _record_failure(self, error: OmpProcessError) -> None:
        if self._failure is None:
            self._failure = error
            logger.error("OMP RPC transport failed: %s", error)
        self._fail_pending(self._failure)
        if not self._closing:
            self._begin_close()

    def _begin_close(self) -> None:
        self._closing = True
        if self._windows_process is not None:
            self._windows_process.closing = True
        if self._close_task is None or (
            self._close_task.done()
            and (
                self._close_task.cancelled() or self._close_task.exception() is not None
            )
        ):
            self._close_task = asyncio.create_task(self.close_process())
            self._close_task.add_done_callback(self._log_close_failure)

    @staticmethod
    def _log_close_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("OMP process cleanup failed", exc_info=error)

    @staticmethod
    def _consume_task(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    async def _cancel_tasks(
        tasks: Sequence[asyncio.Task[None]],
        *,
        timeout: float,
        label: str = "callback",
    ) -> None:
        active = {task for task in tasks if not task.done()}
        for task in active:
            task.cancel()
        if active:
            _, pending = await asyncio.wait(active, timeout=timeout)
            for task in pending:
                task.cancel()
                logger.warning("OMP %s did not honor cancellation", label)
        for task in tasks:
            if task.done():
                OmpRpcClient._consume_task(task)
            else:
                task.add_done_callback(OmpRpcClient._consume_task)

    @staticmethod
    async def _drain(stream: asyncio.StreamReader) -> None:
        while await stream.read(_DRAIN_BYTES):
            pass

    async def _await_contained_exit(self, process: asyncio.subprocess.Process) -> None:
        if not await await_returncode(process, self.process_exit_timeout_s):
            raise OmpProcessError("OMP process did not exit after containment kill")

    async def _settle_spawn(self) -> asyncio.subprocess.Process | None:
        spawning = self._spawn_task
        if spawning is None:
            return self._proc
        try:
            return await asyncio.shield(spawning)
        except Exception:  # noqa: BLE001 -- startup propagates the error; cleanup retains its child.
            return self._proc
        except asyncio.CancelledError:
            if not spawning.cancelled():
                raise
            return self._proc

    async def _close_process(self) -> None:
        turn = self._turn
        self._fail_pending(self._failure or OmpProcessError("OMP RPC client closed"))
        if turn is not None:
            workers = list(turn.callback_tasks)
            if turn.event_worker is not None:
                workers.append(turn.event_worker)
            await self._cancel_tasks(
                workers, timeout=self.callback_exit_timeout_s, label="callback"
            )
            self._survivors.update(task for task in workers if not task.done())

        owner = self._windows_process
        scope = self._process_scope
        owner_closed = False
        if owner is not None and self._proc is None:
            # Stop incomplete helper creation/readiness before awaiting its spawn task.
            try:
                await owner.close(self.process_exit_timeout_s)
                owner_closed = True
            finally:
                self._proc = owner.process
        process = await self._settle_spawn()
        if process is None:
            error = self._retire_survivors()
            if error is not None:
                raise error
            self._windows_process = None
            self._process_scope = None
            return
        if scope is not None:
            await asyncio.to_thread(scope.capture, process)
        if (
            self._failure is None
            and process.returncode is None
            and self._reader_task is not None
            and not self._reader_task.done()
        ):
            try:
                async with asyncio.timeout(_ABORT_TIMEOUT_S):
                    await self._request_frame(
                        OmpCommandName.ABORT, {}, allow_closing=True
                    )
            except (TimeoutError, OmpRpcError, OmpProcessError) as error:
                logger.warning("OMP graceful abort skipped: %s", error)
        reader = self._reader_task
        if reader is not None and not reader.done():
            reader.cancel()
        drains: list[asyncio.Task[None]] = []
        if (
            self._stderr_task is None or self._stderr_task.done()
        ) and process.stderr is not None:
            drains.append(asyncio.create_task(self._drain(process.stderr)))
        try:
            if scope is not None:
                # Ownership lookup can disappear before process exit is observable.
                await asyncio.to_thread(scope.capture, process)
            await self._cancel_tasks(
                list(self._write_tasks),
                timeout=self.callback_exit_timeout_s,
                label="write",
            )
            if not owner_closed:
                # A drain blocked on a full pipe keeps this lock; close must
                # not wait for it. Writers are already cancelled.
                await close_stdin(process)
                await await_returncode(process, self.process_exit_timeout_s)
            await asyncio.to_thread(self.after_stdin_closed)
            if owner is not None:
                if not owner_closed:
                    await owner.close(self.process_exit_timeout_s)
                if scope is not None:
                    await asyncio.to_thread(
                        scope.close, process, self.process_exit_timeout_s
                    )
            else:
                assert scope is not None
                await asyncio.to_thread(scope.capture, process)
                await kill_process_tree(process)
                await asyncio.to_thread(scope.signal)
                await self._await_contained_exit(process)
                await asyncio.to_thread(
                    scope.close, process, self.process_exit_timeout_s
                )
            await self._await_contained_exit(process)
        finally:
            # A cancelled pipe read on Windows stays pending until the child is
            # killed; drain stdout only after that reader finishes.
            if reader is not None:
                await self._cancel_tasks(
                    [reader], timeout=self.callback_exit_timeout_s, label="reader"
                )
            if process.stdout is not None:
                drains.append(asyncio.create_task(self._drain(process.stdout)))
            if self._stderr_task is not None:
                drains.append(self._stderr_task)
            await self._cancel_tasks(
                drains, timeout=self.callback_exit_timeout_s, label="drain"
            )
        error = self._retire_survivors()
        if error is not None:
            raise error
        self._windows_process = None
        self._process_scope = None
