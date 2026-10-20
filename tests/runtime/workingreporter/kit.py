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
