from __future__ import annotations

import asyncio


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll until predicate() is truthy or raise on timeout (non-brittle wait)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


class Recorder:
    """Async report callback that records the sequence of working values."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[bool] = []
        self.fail = fail

    async def __call__(self, working: bool) -> bool:
        self.calls.append(working)
        if self.fail:
            raise RuntimeError("report failed")
        return True


class DetailRecorder:
    """Report callback recording the detail a report would carry when it starts.

    ``hold()`` parks the next report after recording it, until ``release()``.
    """

    def __init__(self) -> None:
        self.detail = "reasoning"
        self.reports: list[str | None] = []
        self.entered = asyncio.Event()
        self._gate: asyncio.Event | None = None
        self._changed = asyncio.Condition()

    def hold(self) -> None:
        self._gate = asyncio.Event()

    def release(self) -> None:
        if self._gate is not None:
            self._gate.set()

    async def __call__(self, working: bool) -> bool:
        async with self._changed:
            self.reports.append(self.detail if working else None)
            self._changed.notify_all()
        gate = self._gate
        if gate is not None:
            self.entered.set()
            await gate.wait()
            self._gate = None
        return True

    async def reports_reach(self, count: int) -> list[str | None]:
        async with self._changed:
            await self._changed.wait_for(lambda: len(self.reports) >= count)
        return self.reports
