"""Correlate the session's native stream with captured execution owners."""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)

from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import (
    CYCLE_CANCEL_GRACE_SECONDS,
    TaskRole,
    TurnTask,
    bind_turn_task,
    resource_tools,
    terminal_deadline,
)
from band.runtime.activity import activity_observer_of
from band.runtime.toolactivity import (
    end_activity,
    require_activity_authority,
    start_memory_activity,
    start_native_tool_activity,
)
from band.runtime.tools.registry import MCP_TOOL_PREFIX

if TYPE_CHECKING:
    from band.integrations.claude_sdk.session_manager import ClaudeRoomSession

logger = logging.getLogger(__name__)

CLAUDE_TOOL_USE_META = "claudecode/toolUseId"
LOCAL_MCP_PREFIX = MCP_TOOL_PREFIX
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped", "killed"})
MAX_PENDING_IDENTITIES = 256
NATIVE_TASK_LIFECYCLE = "band_native_task"


@dataclass(frozen=True)
class Origin:
    tools: AgentToolsProtocol
    owner: TurnTask
    audience: tuple[str, ...]
    release: Callable[[], None] | None = None

    def require_authority(self) -> None:
        if self.owner.deadline is not None or self.owner.task.done():
            raise ValueError("Claude execution is no longer authoritative")
        require_activity_authority(activity_observer_of(self.tools))


@dataclass(eq=False)
class NativeCall:
    origin: Origin
    token: object = field(default_factory=object)
    name: str | None = None


@dataclass(eq=False)
class NativeTask:
    task_id: str
    call_id: str
    origin: Origin
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    owner: TurnTask | None = None
    run_id: str | None = None
    revision: int = 0


class SessionActivity:
    """Use native IDs only to find already admitted foreground/resource owners."""

    def __init__(self, session: ClaudeRoomSession, generation: int) -> None:
        self.session = session
        self.generation = generation
        self._foreground: Origin | None = None
        self._calls: dict[str, NativeCall] = {}
        self._tasks: dict[str, NativeTask] = {}
        self._changed = asyncio.Event()
        self._closed = asyncio.Event()
        self._compaction: tuple[Origin, object] | None = None
        self._identity_waiters = 0
        self._starts = itertools.count(1)

    @contextmanager
    def foreground(self, origin: Origin) -> Iterator[None]:
        if self._foreground is not None:
            raise RuntimeError("Claude already has an admitted foreground query")
        self._foreground = origin
        try:
            yield
        finally:
            self._foreground = None

    def resolve(self, call_id: str) -> Origin:
        if not self.session.retained.may_finish(self.generation):
            raise ValueError("Claude session has retired")
        call = self._calls.get(call_id)
        if call is None:
            raise ValueError("Unknown Claude tool origin")
        origin = self._task_origin(call_id) or call.origin
        origin.require_authority()
        return origin

    def _task_origin(self, call_id: str) -> Origin | None:
        return next(
            (task.origin for task in self._tasks.values() if task.call_id == call_id),
            None,
        )

    async def correlate(
        self, data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        call_id = data.get("tool_use_id") or tool_use_id
        if not isinstance(call_id, str):
            raise ValueError("Claude tool request has no identity")
        origin = await self._hook_origin(data.get("agent_id"))
        origin.require_authority()
        self._remember(call_id, origin)
        return {}

    async def _hook_origin(self, agent_id: Any) -> Origin:
        if not agent_id:
            if self._foreground is None:
                raise ValueError("Claude tool request has no foreground origin")
            return self._foreground
        if self._identity_waiters >= MAX_PENDING_IDENTITIES:
            raise ValueError("Claude identity backlog exceeded")
        self._identity_waiters += 1
        try:
            async with asyncio.timeout(CYCLE_CANCEL_GRACE_SECONDS):
                while self.session.speaks_for(self.generation):
                    if (task := self._tasks.get(agent_id)) is not None:
                        return task.origin
                    self._changed.clear()
                    await self._changed.wait()
        finally:
            self._identity_waiters -= 1
        raise ValueError("Claude task origin is unavailable")

    def _remember(self, call_id: str, origin: Origin) -> NativeCall:
        if (call := self._calls.get(call_id)) is not None:
            return call
        if len(self._calls) >= MAX_PENDING_IDENTITIES:
            raise ValueError("Claude unresolved tool backlog exceeded")
        call = self._calls[call_id] = NativeCall(origin)
        return call

    async def observe(self, message: Any) -> None:
        if isinstance(message, TaskStartedMessage):
            await self._start_task(message)
        elif isinstance(message, (TaskNotificationMessage, TaskUpdatedMessage)):
            await self._finish_task(message)
        elif isinstance(message, (AssistantMessage, UserMessage)):
            await self._observe_blocks(message)
        elif isinstance(message, ResultMessage):
            await self._finish_foreground()
        elif isinstance(message, SystemMessage) and message.subtype == "status":
            await self._status(message.data)

    async def _observe_blocks(self, message: AssistantMessage | UserMessage) -> None:
        if not isinstance(message.content, list):
            return
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                await self._tool_started(message.parent_tool_use_id, block)
            elif isinstance(block, ToolResultBlock):
                await self._tool_finished(block.tool_use_id)

    async def _tool_started(self, parent: str | None, block: ToolUseBlock) -> None:
        call = self._calls.get(block.id)
        if call is None:
            origin = self._task_origin(parent) if parent else self._foreground
            if origin is None or not self.session.speaks_for(self.generation):
                return
            try:
                origin.require_authority()
                call = self._remember(block.id, origin)
            except (ValueError, asyncio.CancelledError):
                return
        if call.name is not None:
            return
        call.name = block.name
        if not block.name.startswith(LOCAL_MCP_PREFIX):
            await start_native_tool_activity(call.origin.tools, call.token, block.name)

    async def _tool_finished(self, call_id: str) -> None:
        if self._task_origin(call_id) is not None:
            return
        if (
            (call := self._calls.pop(call_id, None)) is not None
            and call.name is not None
            and not call.name.startswith(LOCAL_MCP_PREFIX)
        ):
            await end_activity(call.origin.tools, call.token)

    async def _start_task(self, message: TaskStartedMessage) -> None:
        if not self.session.speaks_for(self.generation):
            return
        if (call := self._calls.get(message.tool_use_id or "")) is None:
            return
        existing = self._tasks.get(message.task_id)
        run_id = message.data.get("run_id")
        if existing is not None:
            return
        try:
            call.origin.require_authority()
        except (ValueError, asyncio.CancelledError):
            return
        task = NativeTask(
            message.task_id, message.tool_use_id or "", call.origin, run_id=run_id
        )
        self._admit_task(task)
        if call.name is not None and not call.name.startswith(LOCAL_MCP_PREFIX):
            await start_native_tool_activity(task.origin.tools, call.token, call.name)
        self._diagnose(task, "running")

    def _admit_task(self, task: NativeTask) -> None:
        origin = task.origin
        worker = asyncio.create_task(self._run_task(task))
        task.owner = bind_turn_task(
            origin.tools,
            worker,
            role=TaskRole.RESOURCE,
            request_stop=task.wake.set,
        )
        tools = resource_tools(origin.tools, task.owner)
        task.origin = Origin(tools, task.owner, origin.audience, origin.release)
        self._tasks[task.task_id] = task
        task.revision = next(self._starts)
        self._changed.set()

    async def _run_task(self, task: NativeTask) -> None:
        try:
            await task.wake.wait()
            if not task.terminal.is_set():
                await self._stop_task(task)
        finally:
            if task.terminal.is_set() and self._tasks.get(task.task_id) is task:
                del self._tasks[task.task_id]
                if (call := self._calls.pop(task.call_id, None)) is not None:
                    await end_activity(task.origin.tools, call.token)

    async def _stop_task(self, task: NativeTask) -> None:
        owner = task.owner
        if owner is None:
            self.session.recover("Native task admission was interrupted")
            await self._closed.wait()
            return
        if task.terminal.is_set():
            return
        if (
            self.session.retained.may_finish(self.generation)
            and self._tasks.get(task.task_id) is task
        ):
            try:
                async with asyncio.timeout_at(owner.deadline):
                    client = self.session.client
                    if client is None:
                        raise RuntimeError("Claude task has no client")
                    await client.stop_task(task.task_id)
                    await task.terminal.wait()
                    return
            except Exception:
                logger.warning(
                    "Claude task cleanup requires session retirement", exc_info=True
                )
                self.session.recover("Native task did not confirm termination")
        # The session retains this binding until physical closure. Waiting here
        # takes no I/O lease and cannot block the session's release.
        await self._closed.wait()

    async def _finish_task(self, message: Any) -> None:
        task = self._tasks.get(message.task_id)
        if task is None or message.status not in TERMINAL_TASK_STATUSES:
            return
        run_id = message.data.get("run_id")
        if task.run_id is not None and run_id is not None and task.run_id != run_id:
            return
        task.terminal.set()
        task.wake.set()
        # Remove correlation before the next source-ordered start can reuse ID.
        if self._tasks.get(task.task_id) is task:
            del self._tasks[task.task_id]
        if (call := self._calls.pop(task.call_id, None)) is not None:
            await end_activity(task.origin.tools, call.token)
        self._diagnose(task, message.status)

    def _diagnose(self, task: NativeTask, status: str) -> None:
        logger.info(
            "Claude native task lifecycle",
            extra={
                NATIVE_TASK_LIFECYCLE: {
                    "room": self.session.room_id,
                    "generation": self.generation,
                    "task_id": task.task_id,
                    "tool_use_id": task.call_id,
                    "run_id": task.run_id,
                    "revision": task.revision,
                    "status": status,
                }
            },
        )

    async def _status(self, data: dict[str, Any]) -> None:
        if data.get("status") == "compacting" and self._compaction is None:
            if (origin := self._foreground) is not None:
                token = object()
                self._compaction = (origin, token)
                await start_memory_activity(origin.tools, token)
        elif data.get("status", object()) is None and "permissionMode" not in data:
            await self._end_compaction()

    async def _end_compaction(self) -> None:
        if (compaction := self._compaction) is not None:
            self._compaction = None
            await end_activity(compaction[0].tools, compaction[1])

    async def _finish_foreground(self) -> None:
        await self._end_compaction()
        for call_id, call in tuple(self._calls.items()):
            if call.origin is self._foreground and self._task_origin(call_id) is None:
                await self._tool_finished(call_id)

    def revoke(self) -> None:
        self._changed.set()
        if (
            self._foreground is not None
            and self._foreground.owner.task is not asyncio.current_task()
        ):
            self._foreground.owner.terminate()
        for task in tuple(self._tasks.values()):
            if task.owner is not None:
                task.owner.terminate()

    async def drain(self) -> None:
        deadline = terminal_deadline()
        owners = tuple(
            task.owner for task in self._tasks.values() if task.owner is not None
        )
        for owner in owners:
            owner.terminate(deadline=deadline)
        if owners:
            await asyncio.wait(
                {owner.task for owner in owners},
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )

    async def closed(self) -> None:
        self._closed.set()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.terminal.set()
            task.wake.set()
        for call_id in tuple(self._calls):
            if (call := self._calls.pop(call_id, None)) is not None:
                await end_activity(call.origin.tools, call.token)
        await self._end_compaction()
        if tasks:
            await asyncio.gather(
                *(task.owner.task for task in tasks if task.owner is not None)
            )
