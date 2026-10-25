"""Hold real RPC cleanup and observe cancellation-safe joins without elapsed time."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager

from band.integrations.omprpc import OmpRpcClient


@asynccontextmanager
async def hold_after_stdin_closed(
    client: OmpRpcClient,
) -> AsyncIterator[asyncio.Task[None]]:
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def hold() -> None:
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=10):
            raise TimeoutError("Test did not release after_stdin_closed")

    client.after_stdin_closed = hold
    closing = asyncio.create_task(client.close())
    try:
        async with asyncio.timeout(10):
            await entered.wait()
        yield closing
        if not closing.done():
            release.set()
            await closing
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)


class HeldCleanup:
    def __init__(self, client: OmpRpcClient) -> None:
        self.client = client
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.task: asyncio.Task[object] | None = None
        self.joiners: asyncio.Queue[asyncio.Task[object] | None] = asyncio.Queue()
        closer = client.close_process
        join_close = client.join_close

        async def hold_cleanup() -> None:
            self.task = asyncio.current_task()
            self.entered.set()
            await self.release.wait()
            await closer()
            self.finished.set()

        def observe_join(operation: Awaitable[None]) -> Awaitable[None]:
            waiter = join_close(operation)
            self.joiners.put_nowait(asyncio.current_task())
            return waiter

        client.close_process = hold_cleanup
        client.join_close = observe_join

    async def wait_for_join(self, caller: asyncio.Task[None]) -> None:
        async with asyncio.timeout(5):
            await self.entered.wait()
            joining = await self.joiners.get()
        assert joining is caller
        assert self.task is not None
        assert not self.task.done()
        assert not caller.done()

    async def cancel_and_wait_for_rejoin(self, caller: asyncio.Task[None]) -> None:
        assert caller.cancel()
        await self.wait_for_join(caller)
