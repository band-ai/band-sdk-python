"""Persistent subprocess transport for OMP's native JSONL RPC protocol."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from binascii import Error as Base64Error
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

import psutil
from pydantic import JsonValue, TypeAdapter, ValidationError

from band.integrations.codex.stdio_client import kill_process_tree, session_pid_reused
from band.integrations.codex.winspawn import (
    SpawnFailure,
    WindowsProcess,
    await_returncode,
    close_stdin,
)
from band.integrations.ompwire import (
    OMP_CHUNK_BYTES,
    OMP_FRAME_BYTES,
    OMP_REASSEMBLED_BYTES,
    OmpChunk,
    OmpCommand,
    OmpEvent,
    OmpEventType,
    OmpFrame,
    OmpFrameError,
    OmpFrameType,
    OmpHostToolCall,
    OmpHostToolCancel,
    OmpMessage,
    OmpNegotiation,
    OmpPromptAcceptance,
    OmpPromptResult,
    OmpReady,
    OmpResponse,
    OmpState,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)

logger = logging.getLogger(__name__)

_DRAIN_BYTES = 64 * 1024
_EVENT_BACKLOG_LIMIT = 256
_ABORT_TIMEOUT_S = 1.0
_PROCESS_EXIT_TIMEOUT_S = 1.0
_CALLBACK_EXIT_TIMEOUT_S = 1.0
_PROCESS_SCOPE_VALUE = "1"
_PAYLOAD_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
_EVENT_TYPES = frozenset(OmpEventType)
_UI_NOTIFICATIONS = frozenset(
    {"notify", "setStatus", "setWidget", "setTitle", "set_editor_text", "open_url"}
)


class OmpRpcError(RuntimeError):
    """An unsuccessful command response; the transport remains usable."""

    def __init__(self, command: str, error: str, *, code: str | None = None) -> None:
        self.command = command
        self.error = error
        self.code = code
        super().__init__(f"OMP {command}: {error}")


class OmpProcessError(RuntimeError):
    """The OMP process or its stdio transport is no longer usable."""


class OmpProtocolError(OmpProcessError):
    """An invalid or incorrectly correlated native RPC frame."""


class OmpPromptError(RuntimeError):
    """The prompt or its final background continuation failed or was aborted."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        super().__init__(message)


@dataclass
class PendingChunks:
    chunk_id: str
    count: int
    byte_length: int
    next_index: int
    received_bytes: int
    parts: list[bytes]


@dataclass(frozen=True)
class ReceivedResponse:
    frame: OmpResponse
    sequence: int


@dataclass(frozen=True)
class PendingRequest:
    command: str
    future: asyncio.Future[ReceivedResponse | OmpRpcError | OmpProcessError]


class PromptRun:
    def __init__(
        self,
        request_id: str,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        loop = asyncio.get_running_loop()
        self.id = request_id
        self.on_event = on_event
        self.on_tool = on_tool
        self.on_ui = on_ui
        self.events: asyncio.Queue[OmpEvent] = asyncio.Queue(
            maxsize=_EVENT_BACKLOG_LIMIT
        )
        self.failure: asyncio.Future[BaseException] = loop.create_future()
        self.result: asyncio.Future[OmpPromptResult] = loop.create_future()
        self.changed = asyncio.Event()
        self.last_activity_sequence = 0
        self.settled_sequence = 0
        self.last_assistant: OmpMessage | None = None
        self.rejected = False
        self.callbacks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.callback_tasks: set[asyncio.Task[None]] = set()
        self.event_worker: asyncio.Task[None] | None = None

    def fail(self, error: BaseException) -> None:
        if not self.failure.done():
            self.failure.set_result(error)

    def observe(self, event: OmpEvent, sequence: int) -> None:
        if self.failure.done():
            return
        # Waiting for space would block the reader that services reentrant commands.
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            self.fail(OmpProcessError("OMP event callback backlog exceeded"))
            return
        self.last_activity_sequence = sequence
        if (
            event.type == OmpEventType.MESSAGE_END
            and event.message is not None
            and event.message.role == "assistant"
        ):
            self.last_assistant = event.message
        if event.type == OmpEventType.AGENT_END:
            for message in reversed(event.messages):
                if message.role == "assistant":
                    self.last_assistant = message
                    break
        self.changed.set()


class OmpProcessScope:
    """Retain observable POSIX descendants for best-effort cleanup.

    The private PI_ marker survives native helper filters. Explicit environment
    replacement and restricted process inspection can hide detached children.
    """

    def __init__(self) -> None:
        self.key = f"PI_BAND_PROCESS_{uuid4().hex}"
        self.processes: set[psutil.Process] = set()

    def environment(self, base: Mapping[str, str] | None) -> dict[str, str]:
        return {
            **(os.environ if base is None else base),
            self.key: _PROCESS_SCOPE_VALUE,
        }

    def capture(self, root: asyncio.subprocess.Process) -> None:
        group_visible = not session_pid_reused(root)
        group_members: list[psutil.Process] = []
        for pid in psutil.pids():
            # asyncio's child watcher remains the sole waiter for the root.
            if pid == 0 or pid == root.pid:
                continue
            try:
                member = psutil.Process(pid)
                created = member.create_time()
                marked = False
                try:
                    marked = member.environ().get(self.key) == _PROCESS_SCOPE_VALUE
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                in_group = False
                if group_visible:
                    try:
                        in_group = os.getpgid(pid) == root.pid
                    except (ProcessLookupError, PermissionError):
                        pass
                if not marked and not in_group:
                    continue
                current = psutil.Process(pid)
                if current.create_time() != created or current != member:
                    continue
                if marked:
                    self.processes.add(member)
                elif in_group:
                    group_members.append(member)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if group_visible and not session_pid_reused(root):
            self.processes.update(group_members)

    @staticmethod
    def _is_alive(process: psutil.Process) -> bool:
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def signal(self) -> None:
        for process in self.processes:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass

    def close(self, root: asyncio.subprocess.Process, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        self.capture(root)
        alive = [process for process in self.processes if self._is_alive(process)]
        while alive:
            self.signal()
            psutil.wait_procs(alive, timeout=max(0.0, deadline - time.monotonic()))
            self.capture(root)
            alive = [process for process in self.processes if self._is_alive(process)]
            if alive and time.monotonic() >= deadline:
                raise OmpProcessError(
                    "OMP owned session members survived cleanup: "
                    f"{sorted(process.pid for process in alive)}"
                )


class OmpRpcClient:
    """One OMP process, one persistent reader, and at most one active prompt."""

    def __init__(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> None:
        if not command:
            raise ValueError("OMP command must not be empty")
        self.command = tuple(command)
        self.env = None if env is None else {**os.environ, **env}
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
        self._chunks: PendingChunks | None = None
        self._max_frame_bytes = OMP_FRAME_BYTES
        self._max_reassembled_bytes = OMP_REASSEMBLED_BYTES
        self._pending: dict[str, PendingRequest] = {}
        self._turn: PromptRun | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._prompt_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the supplied command and require native RPC protocol v2."""
        async with self._start_lock:
            if self._closing:
                await self.close()
                self._proc = None
                self._spawn_task = None
                self._close_task = None
            if self._proc is not None:
                self._ensure_running()
                return
            self._failure = None
            self._chunks = None
            self._max_frame_bytes = OMP_FRAME_BYTES
            self._max_reassembled_bytes = OMP_REASSEMBLED_BYTES
            self._closing = False
            self._process_scope = OmpProcessScope() if os.name != "nt" else None
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
                if 2 not in ready.supportedProtocolVersions:
                    raise OmpProtocolError("OMP RPC protocol v2 is required")
                self._max_frame_bytes = ready.maxFrameBytes
                self._max_reassembled_bytes = ready.maxReassembledFrameBytes
                negotiated = await self._request_frame(
                    "negotiate_protocol", {"protocolVersion": 2}
                )
                try:
                    OmpNegotiation.model_validate(self._response_data(negotiated.frame))
                except ValidationError as cause:
                    raise OmpProtocolError(
                        "OMP RPC protocol v2 negotiation failed"
                    ) from cause
            except (OSError, SpawnFailure) as cause:
                await self.close()
                raise OmpProcessError("Unable to start OMP RPC process") from cause
            except (Exception, asyncio.CancelledError):
                await self.close()
                raise

    async def _spawn_process(self) -> asyncio.subprocess.Process:
        if os.name == "nt":
            self._windows_process = WindowsProcess()
            process = await self._windows_process.start_command(
                self.command,
                cwd=None,
                env=self.env,
                limit=OMP_FRAME_BYTES,
            )
        else:
            scope = self._process_scope
            assert scope is not None
            process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=None,
                env=scope.environment(self.env),
                start_new_session=True,
                limit=OMP_FRAME_BYTES,
            )
        self._proc = process
        return process

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

    async def close(self) -> None:
        """Finish shared bounded cleanup before propagating caller cancellation."""
        if self._proc is None and self._spawn_task is None:
            return
        self._begin_close()
        closing = self._close_task
        assert closing is not None
        interrupted = False
        while True:
            try:
                await asyncio.shield(closing)
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
            raise OmpProcessError("OMP RPC process exited")

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
    ) -> ReceivedResponse:
        if not allow_closing:
            self._ensure_running()
        if "id" in parameters or "type" in parameters:
            raise ValueError("OMP command parameters must not override id or type")
        request_id = request_id or self._next_id()
        frame = OmpCommand.model_validate(
            {"type": command, "id": request_id, **parameters}
        )
        future: asyncio.Future[ReceivedResponse | OmpRpcError | OmpProcessError] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = PendingRequest(command, future)
        try:
            await self._write_line(frame.model_dump_json())
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
        try:
            done, _ = await asyncio.wait(
                (work, turn.failure), return_when=asyncio.FIRST_COMPLETED
            )
            if turn.failure in done:
                raise turn.failure.result()
            await work
        except OmpRpcError:
            if not turn.rejected:
                await self.close()
            raise
        except BaseException:
            await self.close()
            raise
        finally:
            self._turn = None
            await self._cancel_tasks([work, event_worker, *turn.callback_tasks])

    async def _complete_prompt(self, turn: PromptRun, message: str) -> None:
        try:
            accepted = await self._request_frame(
                "prompt", {"message": message}, request_id=turn.id
            )
        except OmpRpcError:
            turn.rejected = True
            raise
        try:
            acceptance = OmpPromptAcceptance.model_validate(
                self._response_data(accepted.frame)
            )
        except ValidationError as cause:
            raise OmpProtocolError(
                "Invalid OMP prompt acceptance protocol data"
            ) from cause
        outcome: OmpPromptResult | None = None
        # A synchronous local command explicitly owes no prompt_result.
        if acceptance.agentInvoked is not False:
            outcome = await turn.result
        await self._wait_for_settlement(turn)
        await turn.events.join()
        if turn.callback_tasks:
            _, pending = await asyncio.wait(
                turn.callback_tasks, timeout=_CALLBACK_EXIT_TIMEOUT_S
            )
            if pending:
                raise OmpProcessError("OMP callbacks did not finish after settlement")
        if outcome is not None and outcome.status != "completed":
            message = (
                outcome.error.message
                if outcome.error is not None
                else f"OMP prompt {outcome.status}"
            )
            raise OmpPromptError(outcome.status, message)
        assistant = turn.last_assistant
        if assistant is not None and assistant.stopReason in {"error", "aborted"}:
            message = (
                assistant.errorMessage or f"OMP continuation {assistant.stopReason}"
            )
            raise OmpPromptError(assistant.stopReason, message)

    async def _wait_for_settlement(self, turn: PromptRun) -> None:
        snapshot = await self._request_frame("get_state", {})
        try:
            state = OmpState.model_validate(self._response_data(snapshot.frame))
        except ValidationError as cause:
            raise OmpProtocolError("Invalid OMP settlement protocol data") from cause
        if state.isSettled and snapshot.sequence >= turn.last_activity_sequence:
            return
        # An old, uncorrelated session_settled cannot overrule a newer busy snapshot.
        while (
            turn.settled_sequence <= snapshot.sequence
            or turn.settled_sequence <= turn.last_activity_sequence
        ):
            turn.changed.clear()
            await turn.changed.wait()

    async def _deliver_events(self, turn: PromptRun) -> None:
        try:
            while True:
                event = await turn.events.get()
                try:
                    await turn.on_event(event)
                finally:
                    turn.events.task_done()
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
        await self._write_line(_PAYLOAD_ADAPTER.dump_json(frame).decode("utf-8"))

    def _decode_payload(
        self, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | None:
        frame = OmpFrame.model_validate(payload)
        if frame.type != OmpFrameType.CHUNK:
            if self._chunks is not None:
                raise OmpProtocolError("OMP RPC protocol chunk sequence interrupted")
            return payload
        chunk = OmpChunk.model_validate(payload)
        if chunk.byteLength > self._max_reassembled_bytes:
            raise OmpProtocolError(
                "OMP RPC protocol frame exceeds the negotiated limit"
            )
        data = base64.b64decode(chunk.data, validate=True)
        if (
            not data
            or len(data) > OMP_CHUNK_BYTES
            or base64.b64encode(data).decode("ascii") != chunk.data
        ):
            raise OmpProtocolError("Invalid OMP RPC protocol chunk data")
        if self._chunks is None:
            if chunk.index != 0:
                raise OmpProtocolError(
                    "OMP RPC protocol chunks must start at index zero"
                )
            self._chunks = PendingChunks(
                chunk.chunkId, chunk.count, chunk.byteLength, 0, 0, []
            )
        pending = self._chunks
        if (
            pending.chunk_id != chunk.chunkId
            or pending.count != chunk.count
            or pending.byte_length != chunk.byteLength
            or pending.next_index != chunk.index
        ):
            raise OmpProtocolError("OMP RPC protocol chunk sequence mismatch")
        pending.parts.append(data)
        pending.received_bytes += len(data)
        pending.next_index += 1
        if pending.received_bytes > pending.byte_length:
            raise OmpProtocolError(
                "OMP RPC protocol chunks exceed their declared length"
            )
        if pending.next_index < pending.count:
            return None
        if pending.received_bytes != pending.byte_length:
            raise OmpProtocolError("OMP RPC protocol chunk length mismatch")
        self._chunks = None
        decoded = _PAYLOAD_ADAPTER.validate_json(b"".join(pending.parts), strict=True)
        if OmpFrame.model_validate(decoded).type == OmpFrameType.CHUNK:
            raise OmpProtocolError("OMP RPC protocol chunks cannot nest")
        return decoded

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    if self._chunks is not None:
                        raise OmpProtocolError(
                            "OMP RPC protocol chunks interrupted by stdout EOF"
                        )
                    raise OmpProcessError("OMP RPC stdout EOF")
                if len(line) > self._max_frame_bytes or not line.endswith(b"\n"):
                    raise OmpProtocolError("Invalid OMP RPC protocol frame length")
                payload = self._decode_payload(
                    _PAYLOAD_ADAPTER.validate_json(line, strict=True)
                )
                if payload is None:
                    continue
                self._sequence += 1
                await self._dispatch_payload(payload, self._sequence)
        except asyncio.CancelledError:
            raise
        except ValidationError as cause:
            error = OmpProtocolError("Invalid OMP RPC protocol frame")
            error.__cause__ = cause
            self._record_failure(error)
        except OmpProcessError as error:
            self._record_failure(error)
        except (ValueError, Base64Error) as cause:
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
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(ready)
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
                    turn, "tool", call.id, self._handle_tool(turn, call)
                )
            case OmpFrameType.HOST_TOOL_CANCEL:
                cancellation = OmpHostToolCancel.model_validate(payload)
                if turn is not None:
                    self._cancel_callback(turn, "tool", cancellation.targetId)
            case OmpFrameType.UI_REQUEST:
                request = OmpUIRequest.model_validate(payload)
                if request.method == "cancel":
                    if request.targetId is None:
                        raise OmpProtocolError(
                            "OMP UI cancellation is missing targetId"
                        )
                    if turn is not None:
                        self._cancel_callback(turn, "ui", request.targetId)
                    return
                if request.method in _UI_NOTIFICATIONS:
                    return
                if turn is None:
                    await self._write_frame(
                        {
                            "type": "extension_ui_response",
                            "id": request.id,
                            "cancelled": True,
                        }
                    )
                    return
                self._start_callback(
                    turn, "ui", request.id, self._handle_ui(turn, request)
                )
            case event_type if event_type in _EVENT_TYPES:
                event = OmpEvent.model_validate(payload)
                if turn is not None:
                    turn.observe(event, sequence)

    def _dispatch_response(self, response: OmpResponse, sequence: int) -> None:
        if response.id is None:
            raise OmpProtocolError("OMP response is missing its command id")
        pending = self._pending.get(response.id)
        if pending is None:
            if (
                not response.success
                and self._turn is not None
                and response.id == self._turn.id
            ):
                self._turn.fail(
                    OmpRpcError(
                        response.command,
                        response.error or "Command failed",
                        code=response.code,
                    )
                )
            return
        if response.command != pending.command:
            raise OmpProtocolError("OMP response command does not match its request")
        if pending.future.done():
            return
        if response.success:
            pending.future.set_result(ReceivedResponse(response, sequence))
        else:
            pending.future.set_result(
                OmpRpcError(
                    response.command,
                    response.error or "Command failed",
                    code=response.code,
                )
            )

    def _start_callback(
        self,
        turn: PromptRun,
        kind: str,
        request_id: str,
        callback: Coroutine[object, object, None],
    ) -> None:
        key = (kind, request_id)
        task = asyncio.create_task(callback)
        if key in turn.callbacks:
            task.cancel()
            raise OmpProtocolError("OMP callback request id was reused")
        turn.callbacks[key] = task
        turn.callback_tasks.add(task)
        task.add_done_callback(lambda done: self._callback_finished(turn, key, done))

    @staticmethod
    def _cancel_callback(turn: PromptRun, kind: str, request_id: str) -> None:
        task = turn.callbacks.pop((kind, request_id), None)
        if task is not None:
            task.cancel()

    @staticmethod
    def _callback_finished(
        turn: PromptRun, key: tuple[str, str], task: asyncio.Task[None]
    ) -> None:
        if turn.callbacks.get(key) is task:
            turn.callbacks.pop(key)
        turn.callback_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            turn.fail(error)

    async def _handle_tool(self, turn: PromptRun, call: OmpHostToolCall) -> None:
        result = await turn.on_tool(call)
        if (
            self._closing
            or turn.callbacks.get(("tool", call.id)) is not asyncio.current_task()
        ):
            return
        await self._write_frame(
            {
                "type": "host_tool_result",
                "id": call.id,
                "result": cast(
                    dict[str, JsonValue],
                    result.model_dump(
                        mode="json", exclude={"is_error"}, exclude_none=True
                    ),
                ),
                "isError": result.is_error,
            }
        )

    async def _handle_ui(self, turn: PromptRun, request: OmpUIRequest) -> None:
        response = await turn.on_ui(request)
        if (
            self._closing
            or turn.callbacks.get(("ui", request.id)) is not asyncio.current_task()
        ):
            return
        frame: dict[str, JsonValue] = {
            "type": "extension_ui_response",
            "id": request.id,
        }
        if response.value is not None:
            frame["value"] = response.value
        elif response.confirmed is not None:
            frame["confirmed"] = response.confirmed
        elif response.answers is not None:
            frame["answers"] = [
                answer.model_dump(exclude_none=True) for answer in response.answers
            ]
        else:
            frame["cancelled"] = True
        await self._write_frame(frame)

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while data := await process.stderr.read(_DRAIN_BYTES):
            logger.debug(
                "OMP stderr: %s", data.decode("utf-8", errors="replace").rstrip()
            )

    def _fail_pending(self, error: OmpProcessError) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_result(error)
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(error)
        if self._turn is not None:
            self._turn.fail(error)

    def _record_failure(self, error: OmpProcessError) -> None:
        if self._failure is None:
            self._failure = error
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
            self._close_task = asyncio.create_task(self._close_process())
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
    async def _cancel_tasks(tasks: Sequence[asyncio.Task[None]]) -> None:
        active = {task for task in tasks if not task.done()}
        for task in active:
            task.cancel()
        if active:
            _, pending = await asyncio.wait(active, timeout=_CALLBACK_EXIT_TIMEOUT_S)
            for task in pending:
                task.cancel()
                logger.warning("OMP callback did not honor cancellation")
        for task in tasks:
            if task.done():
                OmpRpcClient._consume_task(task)
            else:
                task.add_done_callback(OmpRpcClient._consume_task)

    @staticmethod
    async def _drain(stream: asyncio.StreamReader) -> None:
        while await stream.read(_DRAIN_BYTES):
            pass

    @staticmethod
    async def _wait_for_exit(process: asyncio.subprocess.Process) -> bool:
        try:
            async with asyncio.timeout(_PROCESS_EXIT_TIMEOUT_S):
                await process.wait()
        except TimeoutError:
            return False
        return True

    async def _settle_spawn(self) -> asyncio.subprocess.Process | None:
        spawning = self._spawn_task
        if spawning is None:
            return self._proc
        try:
            return await asyncio.shield(spawning)
        except (OSError, SpawnFailure):
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
            await self._cancel_tasks(workers)

        owner = self._windows_process
        scope = self._process_scope
        owner_closed = False
        if owner is not None and self._proc is None:
            # Stop incomplete helper creation/readiness before awaiting its spawn task.
            try:
                await owner.close(_PROCESS_EXIT_TIMEOUT_S)
                owner_closed = True
            finally:
                self._proc = owner.process
        process = await self._settle_spawn()
        if process is None:
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
                    await self._request_frame("abort", {}, allow_closing=True)
            except (TimeoutError, OmpRpcError, OmpProcessError):
                pass
        if self._reader_task is not None:
            await self._cancel_tasks([self._reader_task])
        drains: list[asyncio.Task[None]] = []
        if process.stdout is not None:
            drains.append(asyncio.create_task(self._drain(process.stdout)))
        if (
            self._stderr_task is None or self._stderr_task.done()
        ) and process.stderr is not None:
            drains.append(asyncio.create_task(self._drain(process.stderr)))
        try:
            if scope is not None:
                # Ownership lookup can disappear before process exit is observable.
                await asyncio.to_thread(scope.capture, process)
            if not owner_closed:
                await close_stdin(process)
                await await_returncode(process, _PROCESS_EXIT_TIMEOUT_S)
            if owner is not None:
                if not owner_closed:
                    await owner.close(_PROCESS_EXIT_TIMEOUT_S)
            else:
                assert scope is not None
                await asyncio.to_thread(scope.capture, process)
                await kill_process_tree(process)
                await asyncio.to_thread(scope.signal)
                if not await await_returncode(process, _PROCESS_EXIT_TIMEOUT_S):
                    raise OmpProcessError(
                        "OMP process did not exit after containment kill"
                    )
                await asyncio.to_thread(scope.close, process, _PROCESS_EXIT_TIMEOUT_S)
            if not await self._wait_for_exit(process):
                raise OmpProcessError("OMP process did not exit after containment kill")
            self._windows_process = None
            self._process_scope = None
        finally:
            if self._stderr_task is not None:
                drains.append(self._stderr_task)
            await self._cancel_tasks(drains)
