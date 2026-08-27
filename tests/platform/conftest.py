"""Shared test helpers for tests/platform."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


def gated_coroutine() -> tuple[
    Callable[..., Awaitable[None]], asyncio.Event, asyncio.Event
]:
    """A mock coroutine that blocks until released, so a caller can cancel
    it genuinely mid-await instead of before it ever starts.

    Returns ``(side_effect, started, release)`` — set ``side_effect`` as an
    AsyncMock's ``side_effect``, ``await started.wait()`` to know the call is
    truly in-flight, then cancel and/or ``release.set()``.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def side_effect(*args, **kwargs):
        started.set()
        await release.wait()

    return side_effect, started, release
