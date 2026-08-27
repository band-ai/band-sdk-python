"""Shared test helpers for tests/platform."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest


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


@asynccontextmanager
async def cancelled_mid_await(
    gate: AsyncMock, call: Coroutine[Any, Any, None]
) -> AsyncIterator[None]:
    """Run ``call`` as a task, cancel it only once it has genuinely entered
    its await (gated on ``gate``, not before the call ever starts) — the one
    reusable shape behind every "cancel this call mid-flight" test.

    Hands control back once ``call`` is in-flight, so the caller's block can
    inject something else while it's still suspended (e.g. a concurrent
    ``disconnect()``) before cancellation happens. On exit: cancels, asserts
    ``CancelledError`` propagates uncaught, and clears ``gate``'s side effect
    so it reverts to ordinary mock behavior for anything after.
    """
    side_effect, started, _release = gated_coroutine()
    gate.side_effect = side_effect
    task = asyncio.create_task(call)
    await started.wait()
    try:
        yield
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.side_effect = None
