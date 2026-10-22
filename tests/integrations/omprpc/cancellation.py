"""Hold real RPC cleanup and observe cancellation-safe joins without elapsed time."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from band.integrations.omprpc import OmpRpcClient


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
