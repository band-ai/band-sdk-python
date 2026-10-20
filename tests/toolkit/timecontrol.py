"""Explicit virtual-time advancement while real asyncio I/O makes progress."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from looptime import LoopTimeEventLoop

from tests.adapters.waiting import spin
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background


class ControlledClock:
    """Keep time stationary until the test crosses a deliberate boundary."""

    def __init__(self, loop: LoopTimeEventLoop) -> None:
        self.loop = loop

    def advance(self, seconds: float) -> None:
        self.loop.setup_looptime(
            start=self.loop.time() + seconds, resolution=binary_clock_resolution()
        )

    async def elapse(self, seconds: float) -> None:
        """Cross a timer boundary and let its runnable callbacks settle."""
        self.advance(seconds)
        await spin()


async def keep_time_stationary() -> None:
    # A ready task prevents looptime from jumping to a timer during socket I/O.
    while True:
        await asyncio.sleep(0)


@asynccontextmanager
async def controlled_clock() -> AsyncIterator[ControlledClock]:
    loop = asyncio.get_running_loop()
    assert isinstance(loop, LoopTimeEventLoop) and loop.looptime_on
    async with background(keep_time_stationary()):
        yield ControlledClock(loop)
