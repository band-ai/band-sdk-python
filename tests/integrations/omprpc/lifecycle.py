"""Generated host operations against a real RPC peer and owned process tree."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from enum import StrEnum
from unittest.mock import patch

from hypothesis import strategies as st

from band.integrations.omprpc import OmpProcessError
from tests.integrations.omprpc.kit import (
    GatedProcess,
    Observer,
    ProcessGate,
    descendant_client,
    descendant_liveness,
    process_gate_session,
    prompt_task,
    queue_prompt,
    second_prompt,
)

STEP_GUARD_S = 15


class Step(StrEnum):
    PROMPT = "admit a held prompt"
    CANCEL_QUEUED = "cancel a prompt waiting for admission"
    SETTLE = "settle the admitted prompt"
    CANCEL_OWNER = "cancel the admitted prompt"
    CLOSE = "close the transport"
    CRASH = "crash the root before closing"
    CANCEL_START_RESTART = "cancel held spawn while a restart waits"
    CANCEL_CLOSE_RESTART = "cancel close during held spawn while a restart waits"


sequences = st.lists(st.sampled_from(Step), min_size=1, max_size=6)


class SpawnBarrier:
    """Hold publication of the first real subprocess, not its creation."""

    def __init__(
        self, spawn: Callable[..., Awaitable[asyncio.subprocess.Process]]
    ) -> None:
        self.spawn = spawn
        self.created = asyncio.Event()
        self.release = asyncio.Event()
        self.process: asyncio.subprocess.Process | None = None

    async def create(
        self, *command: str, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await self.spawn(*command, **kwargs)
        if self.process is None:
            self.process = process
            self.created.set()
            await self.release.wait()
        return process


async def announced_operation(
    operation: Awaitable[None], entered: asyncio.Event
) -> None:
    entered.set()
    await operation


class RpcLifecycle:
    def __init__(self, gate: ProcessGate, *, detached: bool) -> None:
        self.gate = gate
        self.client = descendant_client(gate, scenario="lifecycle", detached=detached)
        self.live = False
        self.active: asyncio.Task[None] | None = None
        self.observer: Observer | None = None
        self.tasks: list[asyncio.Task[None]] = []
        self.roots: list[asyncio.subprocess.Process] = []
        self.children: list[GatedProcess] = []

    async def record_children(self) -> list[GatedProcess]:
        children = [await self.gate.connection(), await self.gate.connection()]
        self.children.extend(children)
        return children

    async def open(self) -> None:
        if self.live:
            return
        await self.client.start()
        assert self.client._proc is not None
        self.roots.append(self.client._proc)
        await self.record_children()
        self.live = True

    async def hold_prompt(self) -> None:
        await self.open()
        if self.active is not None:
            return
        self.observer = Observer()
        self.active = prompt_task(self.client, self.observer)
        self.tasks.append(self.active)
        await self.observer.wait_for("agent_end")
        assert not self.active.done()
        assert (await self.client.request("get_state"))["isSettled"] is False

    async def assert_dead(
        self, roots: list[asyncio.subprocess.Process], children: list[GatedProcess]
    ) -> None:
        assert all(root.returncode is not None for root in roots)
        assert not any(await descendant_liveness(children))

    async def close(self) -> None:
        await self.client.close()
        if self.active is not None:
            [result] = await asyncio.gather(self.active, return_exceptions=True)
            assert result is None or isinstance(result, OmpProcessError)
            self.active = None
        self.live = False
        await self.assert_dead(self.roots, self.children)

    async def overlap_spawn_and_restart(self, *, cancel_start: bool) -> None:
        await self.close()
        barrier = SpawnBarrier(asyncio.create_subprocess_exec)
        closing: asyncio.Task[None] | None = None
        with patch.object(asyncio, "create_subprocess_exec", barrier.create):
            starting = asyncio.create_task(self.client.start())
            self.tasks.append(starting)
            try:
                await barrier.created.wait()
                assert barrier.process is not None
                old_root = barrier.process
                self.roots.append(old_root)
                old_children = await self.record_children()
                if cancel_start:
                    starting.cancel()
                else:
                    close_entered = asyncio.Event()
                    closing = asyncio.create_task(
                        announced_operation(self.client.close(), close_entered)
                    )
                    self.tasks.append(closing)
                    await close_entered.wait()
                    closing.cancel()
                restart_entered = asyncio.Event()
                restarting = asyncio.create_task(
                    announced_operation(self.client.start(), restart_entered)
                )
                self.tasks.append(restarting)
                await restart_entered.wait()
                assert not restarting.done()
                barrier.release.set()
                [result] = await asyncio.gather(starting, return_exceptions=True)
                assert isinstance(
                    result,
                    asyncio.CancelledError if cancel_start else OmpProcessError,
                )
                if closing is not None:
                    [result] = await asyncio.gather(closing, return_exceptions=True)
                    assert isinstance(result, asyncio.CancelledError)
                await restarting
                assert self.client._proc is not None
                assert self.client._proc is not old_root
                self.roots.append(self.client._proc)
                await self.record_children()
                self.live = True
                await self.assert_dead([old_root], old_children)
                await second_prompt(self.client)
            finally:
                barrier.release.set()

    async def take(self, step: Step) -> None:
        match step:
            case Step.PROMPT:
                await self.hold_prompt()
            case Step.CANCEL_QUEUED:
                await self.hold_prompt()
                queued = await queue_prompt(self.client)
                self.tasks.append(queued)
                queued.cancel()
                [result] = await asyncio.gather(queued, return_exceptions=True)
                assert isinstance(result, asyncio.CancelledError)
                assert self.active is not None and not self.active.done()
                assert (await self.client.request("get_state"))["isSettled"] is False
            case Step.SETTLE:
                await self.hold_prompt()
                await self.client.request("release")
                assert self.active is not None and self.observer is not None
                await self.active
                assert "background complete" in self.observer.assistant_texts
                self.active = None
                assert (await self.client.request("get_state"))["isSettled"] is True
            case Step.CANCEL_OWNER:
                await self.hold_prompt()
                assert self.active is not None
                self.active.cancel()
                [result] = await asyncio.gather(self.active, return_exceptions=True)
                assert isinstance(result, asyncio.CancelledError)
                self.active = None
                await self.close()
            case Step.CLOSE:
                await self.close()
            case Step.CRASH:
                await self.open()
                root = self.client._proc
                assert root is not None
                with suppress(OmpProcessError):
                    await self.client.request("crash")
                await root.wait()
                assert root.returncode == 3
                await self.close()
            case Step.CANCEL_START_RESTART | Step.CANCEL_CLOSE_RESTART:
                await self.overlap_spawn_and_restart(
                    cancel_start=step == Step.CANCEL_START_RESTART
                )
        if self.live:
            assert (await self.client.request("checkpoint"))["pid"] is not None

    async def finish(self) -> None:
        if self.active is not None:
            await self.take(Step.SETTLE)
        await self.close()
        assert all(task.done() for task in self.tasks)
        await self.open()
        await second_prompt(self.client)
        await self.close()

    async def cleanup(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.close()


@asynccontextmanager
async def rpc_lifecycle(*, detached: bool) -> AsyncIterator[RpcLifecycle]:
    async with process_gate_session() as gate:
        session = RpcLifecycle(gate, detached=detached)
        try:
            yield session
        finally:
            await session.cleanup()


async def play(steps: list[Step], *, detached: bool) -> None:
    async with rpc_lifecycle(detached=detached) as session:
        for step in steps:
            async with asyncio.timeout(STEP_GUARD_S):
                await session.take(step)
        async with asyncio.timeout(STEP_GUARD_S):
            await session.finish()
