"""Hold real RPC cleanup and observe cancellation-safe joins without elapsed time."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

import pytest

from band.integrations.omprpc import OmpRpcClient

Result = TypeVar("Result")


class HeldCleanup:
    def __init__(self, client: OmpRpcClient, monkeypatch: pytest.MonkeyPatch) -> None:
        self.client = client
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.task: asyncio.Task[object] | None = None
        self.joiners: asyncio.Queue[asyncio.Task[object] | None] = asyncio.Queue()
        close_process = client._close_process
        shield = asyncio.shield

        async def hold_cleanup() -> None:
            self.task = asyncio.current_task()
            self.entered.set()
            await self.release.wait()
            await close_process()
            self.finished.set()

        def observe_shield(operation: Awaitable[Result]) -> asyncio.Future[Result]:
            waiter = shield(operation)
            if operation is client._close_task:
                # Queueing does not suspend the caller: it reaches the real shield
                # wait before the observing test can resume.
                self.joiners.put_nowait(asyncio.current_task())
            return waiter

        monkeypatch.setattr(client, "_close_process", hold_cleanup)
        monkeypatch.setattr(asyncio, "shield", observe_shield)

    async def wait_for_join(self, caller: asyncio.Task[None]) -> None:
        async with asyncio.timeout(5):
            await self.entered.wait()
            joining = await self.joiners.get()
        assert joining is caller
        assert self.task is not None
        assert self.client._close_task is self.task
        assert not self.task.done()
        assert self.task.cancelling() == 0
        assert not caller.done()

    async def cancel_and_wait_for_rejoin(self, caller: asyncio.Task[None]) -> None:
        cancellations = caller.cancelling()
        assert caller.cancel()
        # The next join is emitted only after close catches this cancellation
        # and suspends on a fresh shield around the still-held cleanup task.
        await self.wait_for_join(caller)
        assert caller.cancelling() == cancellations + 1
