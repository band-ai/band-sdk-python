"""One bounded wait for state a test cannot hold at a barrier."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

# Real time under a wall clock, instant under looptime; either way it bounds a
# wait that would otherwise never end instead of counting loop turns.
UNTIL_TIMEOUT_S = 15.0
POLL_STEP_S = 0.001


async def until(done: Callable[[], bool], *, failure: str) -> None:
    """Wait until ``done`` holds, failing with ``failure`` if it never does."""
    try:
        async with asyncio.timeout(UNTIL_TIMEOUT_S):
            while not done():
                await asyncio.sleep(POLL_STEP_S)
    except TimeoutError:
        raise AssertionError(failure) from None
