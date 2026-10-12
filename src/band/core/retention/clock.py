"""The time source idle deadlines are measured against."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class RetentionClock(Protocol):
    def now(self) -> float: ...

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None: ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None:
        timeout = None if deadline is None else max(0, deadline - self.now())
        try:
            async with asyncio.timeout(timeout):
                await changed.wait()
        except TimeoutError:
            pass
