"""Gated native startup and close boundaries for retention composition."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from band.runtime.mailbox import RoomMailbox
from tests.adapters.hold import Hold
from tests.adapters.omp.kit import RecordingRuntime


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
