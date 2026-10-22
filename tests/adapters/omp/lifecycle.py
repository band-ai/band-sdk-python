from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum, auto
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from pytest import MonkeyPatch, raises

from band.adapters.omp import OmpAdapter
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import Launches, PendingRuntime, SignallingLock, run_turn

ROOM = "room-123"


class Step(StrEnum):
    TURN = auto()
    CLEANUP = auto()
    FAILED_CLOSE = auto()
    RETIRE_QUEUED = auto()
    CANCEL_CLEANUP_WAITER = auto()


class HeldClose:
    def __init__(self, close: Callable[[], Awaitable[None]]) -> None:
        self.close = close
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self) -> None:
        self.entered.set()
        await self.release.wait()
        await self.close()


class RoomLifecycle:
    def __init__(self, workspace: Path, launches: Launches) -> None:
        self.launches = launches
        self.adapter = OmpAdapter(
            workspace_for_room=lambda _room: str(workspace), emit=()
        )
        self.lock = SignallingLock()
        self.adapter._locks[ROOM] = self.lock
        self.expected: PendingRuntime | None = None
        self.retiring = False

    async def take(self, step: Step) -> None:
        match step:
            case Step.TURN:
                await self.turn()
            case Step.CLEANUP:
                await self.cleanup()
            case Step.FAILED_CLOSE:
                await self.failed_close()
            case Step.RETIRE_QUEUED | Step.CANCEL_CLEANUP_WAITER:
                await self.retire_queued(
                    cancel_waiter=step == Step.CANCEL_CLEANUP_WAITER
                )
        self.assert_custody()

    async def turn(self) -> None:
        previous = self.expected
        fresh = previous is None or self.retiring
        tools = FakeAgentTools()
        await run_turn(self.adapter, tools)
        assert tools.turn.replied
        assert not reported_failures(tools)
        current = self.launches.runtimes[-1]
        if fresh:
            assert current is not previous
            if previous is not None:
                assert previous.closed
        else:
            assert current is previous
        self.expected = current
        self.retiring = False

    async def cleanup(self) -> None:
        await self.adapter.cleanup_all()
        self.expected = None
        self.retiring = False

    async def failed_close(self) -> None:
        if self.expected is None or self.retiring:
            await self.turn()
        runtime = self.expected
        assert runtime is not None
        close = runtime.close
        runtime.close = AsyncMock(side_effect=OSError("process still alive"))
        try:
            with raises(OSError, match="still alive"):
                await self.adapter.on_cleanup(ROOM)
        finally:
            runtime.close = close
        self.retiring = True

    async def retire_queued(self, *, cancel_waiter: bool) -> None:
        if self.expected is None or self.retiring:
            await self.turn()
        runtime = self.expected
        assert runtime is not None
        runtime.finish.clear()
        runtime.yielded.clear()
        self.lock.contended.clear()
        close = runtime.close
        held = HeldClose(close)
        runtime.close = held  # type: ignore[method-assign]
        tasks: list[asyncio.Task[None]] = []
        try:
            active = asyncio.create_task(run_turn(self.adapter, FakeAgentTools()))
            tasks.append(active)
            await runtime.yielded.wait()
            queued_tools = FakeAgentTools()
            queued = asyncio.create_task(run_turn(self.adapter, queued_tools))
            tasks.append(queued)
            await self.lock.contended.wait()
            cleanup = asyncio.create_task(self.adapter.on_cleanup(ROOM))
            tasks.append(cleanup)
            await held.entered.wait()
            assert not runtime.closed
            assert self.launches.plugins[-1].exists()
            if cancel_waiter:
                cleanup.cancel()
                with raises(asyncio.CancelledError):
                    await cleanup
                assert not runtime.closed
                assert self.launches.plugins[-1].exists()
            held.release.set()
            for turn in (active, queued):
                with raises(asyncio.CancelledError):
                    await turn
            assert not queued_tools.turn.replied
            if not cancel_waiter:
                await cleanup
            await self.cleanup()
        finally:
            held.release.set()
            runtime.close = close
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.adapter.cleanup_all()

    def assert_custody(self) -> None:
        if self.expected is None:
            assert not self.adapter._rooms
            assert not self.adapter._workspaces.rooms
            assert all(runtime.closed for runtime in self.launches.runtimes)
            assert all(not overlay.exists() for overlay in self.launches.plugins)
            return
        assert self.adapter._rooms[ROOM].runtime is self.expected
        assert self.launches.plugins[-1].exists()
        with raises(ValueError):
            self.adapter._workspaces.claim("another-room")
        assert all(runtime.closed for runtime in self.launches.runtimes[:-1])
        assert all(not overlay.exists() for overlay in self.launches.plugins[:-1])


async def play(steps: list[Step]) -> None:
    with (
        TemporaryDirectory(prefix="band-omp-model-") as directory,
        MonkeyPatch.context() as patch,
    ):
        launches = Launches()
        patch.setattr("band.adapters.omp.OmpRpcClient", launches)
        model = RoomLifecycle(Path(directory), launches)
        try:
            async with asyncio.timeout(15):
                for step in steps:
                    await model.take(step)
                await model.cleanup()
                model.assert_custody()
        finally:
            await model.adapter.cleanup_all()
