"""Gated native startup and close boundaries for retention composition."""

from __future__ import annotations

import asyncio
from typing import Self

from band.adapters.omp import RoomGate
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


class ObservedRoomGate(RoomGate):
    """Report entry attempts while preserving production callback reentry."""

    def __init__(self) -> None:
        super().__init__()
        self.attempted = asyncio.Event()

    async def __aenter__(self) -> Self:
        self.attempted.set()
        return await super().__aenter__()
