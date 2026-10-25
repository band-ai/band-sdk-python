"""Holds that pause a mailbox job until the test releases it."""

from __future__ import annotations

import asyncio


class JobHold:
    """One pause point: the job sets ``entered``, then waits for ``release``."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    async def pause(self) -> None:
        self.entered.set()
        await self.release.wait()


class SettlingJob:
    """Delay cancellation cleanup and optionally suppress or replace its error."""

    def __init__(self, error: BaseException | None) -> None:
        self.running = JobHold()
        self.running.hold()
        self.settling = JobHold()
        self.settling.hold()
        self.finished = asyncio.Event()
        self.error = error

    async def run(self) -> str:
        try:
            await self.running.pause()
        except asyncio.CancelledError:
            try:
                await self.settling.pause()
                if self.error is not None:
                    raise self.error
                return "cancellation suppressed"
            finally:
                self.finished.set()
        return "completed"


async def checkpoint() -> None:
    """Let already scheduled task wakeups run before resuming the test."""
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await reached.wait()
