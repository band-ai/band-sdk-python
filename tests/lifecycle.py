"""Generic async lifecycle helpers for tests driving a background
server or task end to end."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from typing import Any, Protocol


class Startable(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@asynccontextmanager
async def running(server: Startable) -> AsyncIterator[Startable]:
    """Start ``server``, yield it, and always stop it -- even on failure."""
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@asynccontextmanager
async def backgrounded(
    coro: Coroutine[Any, Any, object],
) -> AsyncIterator[asyncio.Task[object]]:
    """Run ``coro`` as a background task for the block; always cancelled
    and awaited afterward, even on failure."""
    task = asyncio.create_task(coro)
    try:
        yield task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def held_open(
    connect: Callable[[asyncio.Event], Awaitable[None]],
) -> AsyncIterator[None]:
    """Run ``connect`` in the background until it signals its ready event,
    keeping whatever connection it opens alive for the block."""
    ready = asyncio.Event()
    async with backgrounded(connect(ready)):
        await asyncio.wait_for(ready.wait(), timeout=5.0)
        yield


async def elapsed(coro: Awaitable[None]) -> float:
    """Wall-clock seconds ``coro`` took -- for asserting a bounded-shutdown
    guarantee without an external ``wait_for`` that would mask a real hang
    by cancelling the call from outside."""
    start = asyncio.get_running_loop().time()
    await coro
    return asyncio.get_running_loop().time() - start
