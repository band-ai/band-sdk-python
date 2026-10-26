"""Gated native startup and close boundaries for retention composition."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, TypeVar

from band.adapters.omp import (
    OmpAdapter,
)
from band.integrations.omprpc import OmpProcessError
from band.runtime.mailbox import RoomMailbox
from tests.adapters.hold import Hold
from tests.adapters.omp.models import RecordingRuntime
from tests.adapters.omp.runtime import PendingRuntime


class StartupGate:
    """Hold native startup readiness until the test releases it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, *_args: object, **_kwargs: object) -> None:
        self.entered.set()
        await self.release.wait()


class HungStartRuntime(PendingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.started = asyncio.Event()

    async def start(self):
        self.started.set()
        await asyncio.Event().wait()


class BoomCloseRuntime(PendingRuntime):
    async def close(self):
        raise OSError("process still alive")


class FirstCloseFailsRuntime(RecordingRuntime):
    """Fail the first close so custody stays with this process, then succeed."""

    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self._close_failed = False
        self.reject_requests = False

    async def request(self, command, **parameters):
        if self.reject_requests:
            raise OmpProcessError("client is closing")
        return await super().request(command, **parameters)

    async def close(self):
        if not self._close_failed:
            self._close_failed = True
            raise OSError("process still alive")
        await super().close()


class SameTaskCleanupRuntime(PendingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.adapter: OmpAdapter | None = None
        self.cleaned_from_turn = False

    async def _prompt_body(self, _message, *, on_event, on_tool, on_ui):
        assert self.adapter is not None
        await self.adapter.on_cleanup("room-123")
        self.cleaned_from_turn = True


class Launches:
    """Record native runtime instances and the startup overlays they own."""

    def __init__(self) -> None:
        self.runtimes: list[RecordingRuntime] = []
        self.servers: list[dict[str, object]] = []
        self.plugins: list[Path] = []
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.spawned = asyncio.Event()
        self.fail_first_close = False
        self.runtime_factory: Callable[[], RecordingRuntime] = RecordingRuntime

    def __call__(
        self, command: list[str], env=None, **_kwargs: object
    ) -> RecordingRuntime:
        plugin = command[command.index("--plugin-dir") + 1]
        overlay = json.loads((Path(plugin) / ".mcp.json").read_text(encoding="utf-8"))
        self.servers.append(overlay["mcpServers"])
        self.plugins.append(Path(plugin))
        self.commands.append(list(command))
        self.envs.append(None if env is None else dict(env))
        runtime = (
            FirstCloseFailsRuntime()
            if self.fail_first_close
            else self.runtime_factory()
        )
        self.fail_first_close = False
        runtime.finish.set()
        self.runtimes.append(runtime)
        self.spawned.set()
        return runtime


class SignallingLock(asyncio.Lock):
    """A room lock that reports when a second turn starts waiting for it."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        if self.locked():
            self.contended.set()
        return await super().acquire()


def failing_runtime(closing: asyncio.Event, finish_close: asyncio.Event):
    class FailingRuntime(PendingRuntime):
        async def _prompt_body(self, *_args, **_kwargs):
            raise RuntimeError("native turn failed")

        async def close(self):
            closing.set()
            await finish_close.wait()
            self.closed = True

    return FailingRuntime()


class SettlingStartRuntime(RecordingRuntime):
    def __init__(self, *, clear_cancel: bool) -> None:
        super().__init__()
        self.starting = Hold()
        self.settling = Hold()
        self.clear_cancel = clear_cancel
        self.cancellations = 0

    async def start(self) -> None:
        try:
            await self.starting.wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            if self.clear_cancel:
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()
            try:
                await self.settling.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise


class HeldCloseRuntime(RecordingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.closing = Hold()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        await self.closing.wait()
        await super().close()


class SignallingCloseRuntime(RecordingRuntime):
    """Wake an independent caller at the physical-close boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.physical_close = asyncio.Event()

    async def close(self) -> None:
        await super().close()
        self.physical_close.set()


Result = TypeVar("Result")


class ObservedMailbox(RoomMailbox):
    """Report submissions while preserving production mailbox scheduling."""

    def __init__(self) -> None:
        super().__init__()
        self.attempted = asyncio.Event()

    async def ask(self, room_id: str, run: Callable[[], Awaitable[Result]]) -> Result:
        self.attempted.set()
        return await super().ask(room_id, run)


class HeldWaiterMailbox(RoomMailbox):
    """Pause selected waiters before submission or after their real jobs finish."""

    def __init__(self, *, before_submit: bool = False) -> None:
        super().__init__()
        self.before_submit = before_submit
        self.waiters: deque[Hold | None] = deque()

    async def ask(self, room_id: str, run: Callable[[], Awaitable[Result]]) -> Result:
        hold = self.waiters.popleft() if self.waiters else None
        if hold is not None and self.before_submit:
            await hold.wait()
        result = await super().ask(room_id, run)
        if hold is not None and not self.before_submit:
            await hold.wait()
        return result
