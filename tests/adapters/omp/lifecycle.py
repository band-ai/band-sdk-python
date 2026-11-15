"""Model native OMP lifecycle sequences for the collected lifecycle test."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum, auto
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from pytest import MonkeyPatch, raises

from band.adapters.omp import OmpAdapter
from band.core.model_catalog import ModelSelection
from band.integrations.omprpc import OmpProcessError
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.hold import Hold
from tests.adapters.omp.custody import claimed_workspace_rooms
from tests.adapters.omp.lifecyclekit import HeldWaiterMailbox, Launches, SignallingLock
from tests.adapters.omp.mailboxkit import (
    CallbackResolver,
    MailboxTools,
    mailbox_adapter,
    peer_frames,
    room_client,
    selection_order,
)
from tests.adapters.omp.mailboxpeer import (
    CALLBACK_MODEL,
    FIRST_CALLBACK,
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    PUBLIC_MODEL,
    REJECTED_MODEL,
    PeerCommand,
    model_reply,
)
from tests.adapters.omp.retirementkit import native_overlay
from tests.adapters.omp.runtime import PendingRuntime
from tests.adapters.omp.turns import run_turn
from tests.integrations.omprpc.processes import owned_rpc_process

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
        patch.setattr("band.adapters.omp.lifecycle.OmpRpcClient", launches)
        model = RoomLifecycle(Path(directory), launches)
        try:
            async with asyncio.timeout(15):
                for step in steps:
                    await model.take(step)
                await model.cleanup()
                model.assert_custody()
        finally:
            await model.adapter.cleanup_all()


class NativeStep(StrEnum):
    TURN = auto()
    SELECT = auto()
    CLEANUP = auto()
    INTERRUPT_ADMISSION = auto()


class NativeCallbackLifecycle:
    """Compose native callback reentry, retained choices, and physical cleanup."""

    def __init__(self, workspace: Path) -> None:
        self.resolver = CallbackResolver()
        self.adapter = mailbox_adapter(workspace, self.resolver)
        self.resolver.adapter = self.adapter
        self.selected = ORIGINAL_MODEL
        self.process: asyncio.subprocess.Process | None = None
        self.overlay: Path | None = None
        self.retired: list[tuple[asyncio.subprocess.Process, Path]] = []

    async def take(self, step: NativeStep) -> None:
        match step:
            case NativeStep.TURN:
                await self.turn()
            case NativeStep.SELECT:
                await self.adapter.apply_model_selection(
                    ModelSelection(model=f"{MODEL_PROVIDER}/{PUBLIC_MODEL}"),
                    room_id=ROOM,
                )
                self.selected = PUBLIC_MODEL
            case NativeStep.CLEANUP:
                await self.cleanup()
            case NativeStep.INTERRUPT_ADMISSION:
                await self.interrupt_admission()
        self.assert_custody()

    async def turn(self) -> None:
        previous = self.process
        initial_model = self.selected
        tools = MailboxTools()
        await run_turn(self.adapter, tools, bootstrap=previous is None)
        client = room_client(self.adapter)
        process = owned_rpc_process(client)
        if previous is None:
            self.selected = CALLBACK_MODEL
            assert selection_order(await peer_frames(self.adapter)) == [
                initial_model,
                CALLBACK_MODEL,
                FIRST_CALLBACK,
            ]
        else:
            assert process is previous
        self.process = process
        self.overlay = native_overlay(client)
        assert tools.chat == [model_reply(self.selected)]
        assert not reported_failures(tools)

    async def cleanup(self) -> None:
        await self.adapter.cleanup_all()
        if self.process is not None:
            assert self.overlay is not None
            self.retired.append((self.process, self.overlay))
            self.selected = ORIGINAL_MODEL
        self.process = None
        self.overlay = None

    async def interrupt_admission(self) -> None:
        if self.process is None:
            await self.turn()
        client = room_client(self.adapter)
        process, overlay = self.process, self.overlay
        assert process is not None and overlay is not None
        await client.request(PeerCommand.HOLD_MODEL_RESPONSE, modelId=REJECTED_MODEL)
        mailbox = HeldWaiterMailbox(before_submit=True)
        hold = Hold()
        mailbox.waiters.extend((None, None, hold))
        self.adapter._mailbox = mailbox
        tools = MailboxTools()
        turn = asyncio.create_task(run_turn(self.adapter, tools, bootstrap=False))
        selecting: asyncio.Task[None] | None = None
        try:
            await hold.arrived()
            selecting = asyncio.create_task(
                self.adapter.apply_model_selection(
                    ModelSelection(model=f"{MODEL_PROVIDER}/{REJECTED_MODEL}"),
                    room_id=ROOM,
                )
            )
            await client.request(PeerCommand.WAIT_MODEL_RESPONSE)
            selecting.cancel()
            with raises(asyncio.CancelledError):
                await selecting
            hold.released.set()
            with raises(OmpProcessError):
                await turn
            assert tools.chat == []
            assert reported_failures(tools)
            self.retired.append((process, overlay))
            self.process, self.overlay = None, None
        finally:
            hold.released.set()
            tasks = [turn, *([selecting] if selecting is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def assert_custody(self) -> None:
        for process, overlay in self.retired:
            assert process.returncode is not None
            assert not overlay.exists()
        if self.process is None:
            assert claimed_workspace_rooms(self.adapter) == ()
            return
        assert self.process.returncode is None
        assert self.overlay is not None and self.overlay.exists()
        assert claimed_workspace_rooms(self.adapter) == (ROOM,)


async def play_native(steps: list[NativeStep]) -> None:
    with TemporaryDirectory(prefix="band-omp-native-model-") as directory:
        model = NativeCallbackLifecycle(Path(directory))
        try:
            for step in steps:
                await model.take(step)
            await model.cleanup()
            model.assert_custody()
        finally:
            await model.adapter.cleanup_all()
