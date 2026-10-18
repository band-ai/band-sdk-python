"""Explicit virtual-time advancement while real asyncio I/O makes progress."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from looptime import LoopTimeEventLoop

from tests.toolkit.clock import binary_clock_resolution


class ControlledClock:
    """Keep time stationary until the test crosses a deliberate boundary."""

    def __init__(self, loop: LoopTimeEventLoop) -> None:
        self.loop = loop

    def advance(self, seconds: float) -> None:
        self.loop.setup_looptime(
            start=self.loop.time() + seconds, resolution=binary_clock_resolution()
        )


async def keep_time_stationary() -> None:
    # A ready task prevents looptime from jumping to a timer during socket I/O.
    while True:
        await asyncio.sleep(0)


@asynccontextmanager
async def controlled_clock() -> AsyncIterator[ControlledClock]:
    loop = asyncio.get_running_loop()
    assert isinstance(loop, LoopTimeEventLoop) and loop.looptime_on
    clock_task = asyncio.create_task(keep_time_stationary())
    try:
        yield ControlledClock(loop)
    finally:
        clock_task.cancel()
        with suppress(asyncio.CancelledError):
            await clock_task
