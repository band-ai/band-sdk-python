"""Kit: a probe that measures how many tasks overlap inside a code path."""

from __future__ import annotations

import asyncio


class ConcurrencyProbe:
    """An async context manager that records the peak number of tasks inside it.

    Entering yields control once — as a real REST post would — so any
    concurrently dispatched task gets the chance to enter and be counted.
    A serialized code path keeps ``peak`` at 1.
    """

    def __init__(self) -> None:
        self._active = 0
        self.peak = 0

    async def __aenter__(self) -> None:
        self._active += 1
        self.peak = max(self.peak, self._active)
        await asyncio.sleep(0)

    async def __aexit__(self, *exc: object) -> None:
        self._active -= 1
