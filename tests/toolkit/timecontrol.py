"""Explicit virtual-time advancement while real asyncio I/O makes progress."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Coroutine
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, TypeVar

from looptime import LoopTimeEventLoop

from tests.adapters.waiting import spin
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background

Result = TypeVar("Result")

REAL_IO_STEP_S = 0.01


class ControlledClock:
    """Keep time stationary until the test crosses a deliberate boundary."""

    def __init__(self, loop: LoopTimeEventLoop, stationary: asyncio.Task[None]) -> None:
        self.loop = loop
        self._stationary = stationary

    def advance(self, seconds: float) -> None:
        self.loop.setup_looptime(
            start=self.loop.time() + seconds, resolution=binary_clock_resolution()
        )

    async def elapse(self, seconds: float) -> None:
        """Cross a timer boundary and let its runnable callbacks settle."""
        self.advance(seconds)
        await spin()

    async def _pause(self) -> None:
        self._stationary.cancel()
        await asyncio.gather(self._stationary, return_exceptions=True)

    async def run_io(self, operation: Awaitable[Result]) -> Result:
        """Let real subprocess readiness and retirement make timed progress."""
        # Edge-triggered I/O cannot block on a second infinite selector poll
        # before the loop services the completion returned by the first one.
        self.loop.setup_looptime(
            start=self.loop.time(),
            resolution=binary_clock_resolution(),
            idle_step=REAL_IO_STEP_S,
        )
        running = asyncio.ensure_future(operation)
        release = threading.Event()
        # Looptime follows real time while an executor operation is pending.
        # Hold that mode until the whole I/O operation, including cleanup, ends.
        waiting = asyncio.create_task(asyncio.to_thread(release.wait))
        try:
            await self._pause()
            return await running
        finally:
            try:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            finally:
                release.set()
                await waiting
                self._stationary = asyncio.create_task(keep_time_stationary())

    @asynccontextmanager
    async def background(
        self, operation: Coroutine[Any, Any, Result] | asyncio.Task[Result]
    ) -> AsyncIterator[asyncio.Task[Result]]:
        """Join a native operation with real I/O progress on exceptional exits."""
        owner = AsyncExitStack()
        try:
            task = await owner.enter_async_context(background(operation))
            yield task
        finally:
            await self.run_io(owner.aclose())


async def keep_time_stationary() -> None:
    # A ready task prevents looptime from jumping to a timer during socket I/O.
    while True:
        await asyncio.sleep(0)


@asynccontextmanager
async def controlled_clock() -> AsyncIterator[ControlledClock]:
    loop = asyncio.get_running_loop()
    assert isinstance(loop, LoopTimeEventLoop) and loop.looptime_on
    async with background(keep_time_stationary()) as stationary:
        clock = ControlledClock(loop, stationary)
        try:
            yield clock
        finally:
            await clock._pause()
