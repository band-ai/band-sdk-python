"""A live ``OmpRpcClient`` driven step by step against a real RPC peer.

This is the model for ``test_lifecycle_model.py``, not a collected test.
Hypothesis picks a short sequence of ``Step`` values — the operations a host
can issue — and ``RpcLifecycle.take`` applies each one to one client and the
process tree it owns. After the sequence, the client must still start, finish
a turn, and reap descendants.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from unittest.mock import patch

from hypothesis import strategies as st

from band.integrations.omprpc import OmpProcessError
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeer import BACKGROUND_COMPLETE, CRASH_EXIT
from tests.integrations.omprpc.kit import (
    GatedProcess,
    Observer,
    ProcessGate,
    SpawnBarrier,
    descendant_client,
    descendant_liveness,
    descendants_started_with,
    owned_rpc_process,
    process_gate_session,
    prompt_task,
    queue_prompt,
    second_prompt,
)

# A healthy step takes milliseconds; a hung one fails its example within this.
STEP_GUARD_S = 15


class Step(StrEnum):
    """Host operations Hypothesis may interleave."""

    PROMPT = "admit a held prompt"
    CANCEL_QUEUED = "cancel a prompt waiting for admission"
    SETTLE = "settle the admitted prompt"
    CANCEL_OWNER = "cancel the admitted prompt"
    CLOSE = "close the transport"
    CRASH = "crash the root before closing"
    CANCEL_START_RESTART = "cancel held spawn while a restart waits"
    CANCEL_CLOSE_RESTART = "cancel close during held spawn while a restart waits"


sequences = st.lists(st.sampled_from(Step), min_size=1, max_size=6)


@dataclass
class HeldSpawn:
    starting: asyncio.Task[None]
    root: asyncio.subprocess.Process
    children: list[GatedProcess]
    barrier: SpawnBarrier

    def release(self) -> None:
        self.barrier.release.set()


async def announced_operation(
    operation: Awaitable[None], entered: asyncio.Event
) -> None:
    entered.set()
    await operation


class RpcLifecycle:
    """One RPC client, the process trees it has owned, and at most one admitted turn."""

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
        children = await descendants_started_with(self.gate, self.roots[-1])
        self.children.extend(children)
        return children

    async def open(self) -> None:
        if self.live:
            return
        await self.client.start()
        self.roots.append(owned_rpc_process(self.client))
        await self.record_children()
        self.live = True

    async def hold_prompt(self) -> None:
        await self.open()
        if self.active is not None:
            return
        self.observer = Observer()
        self.active = prompt_task(self.client, self.observer)
        self.tasks.append(self.active)
        await self.observer.wait_for(OmpEventType.AGENT_END)
        assert not self.active.done()
        assert (await self.client.request("get_state"))["isSettled"] is False

    async def cancel_queued(self) -> None:
        await self.hold_prompt()
        queued = await queue_prompt(self.client)
        self.tasks.append(queued)
        queued.cancel()
        [result] = await asyncio.gather(queued, return_exceptions=True)
        assert isinstance(result, asyncio.CancelledError)
        assert self.active is not None and not self.active.done()
        assert (await self.client.request("get_state"))["isSettled"] is False

    async def settle(self) -> None:
        await self.hold_prompt()
        await self.client.request("release")
        assert self.active is not None and self.observer is not None
        await self.active
        assert BACKGROUND_COMPLETE in self.observer.assistant_texts
        self.active = None
        assert (await self.client.request("get_state"))["isSettled"] is True

    async def cancel_owner(self) -> None:
        await self.hold_prompt()
        assert self.active is not None
        self.active.cancel()
        [result] = await asyncio.gather(self.active, return_exceptions=True)
        assert isinstance(result, asyncio.CancelledError)
        self.active = None
        await self.close()

    async def crash(self) -> None:
        await self.open()
        root = owned_rpc_process(self.client)
        with suppress(OmpProcessError):
            await self.client.request("crash")
        await root.wait()
        assert root.returncode == CRASH_EXIT
        await self.close()

    async def assert_reaped(
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
        await self.assert_reaped(self.roots, self.children)

    def _track(self, task: asyncio.Task[None]) -> asyncio.Task[None]:
        self.tasks.append(task)
        return task

    async def _assert_finished(
        self, task: asyncio.Task[None], error: type[BaseException]
    ) -> None:
        [result] = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result, error)

    async def _queue_close(self) -> asyncio.Task[None]:
        entered = asyncio.Event()
        closing = self._track(
            asyncio.create_task(announced_operation(self.client.close(), entered))
        )
        await entered.wait()
        return closing

    async def _queue_restart(self) -> asyncio.Task[None]:
        entered = asyncio.Event()
        restarting = self._track(
            asyncio.create_task(announced_operation(self.client.start(), entered))
        )
        await entered.wait()
        assert not restarting.done()
        return restarting

    async def _claim_restart(
        self,
        restarting: asyncio.Task[None],
        old_root: asyncio.subprocess.Process,
        old_children: list[GatedProcess],
    ) -> None:
        await restarting
        process = owned_rpc_process(self.client)
        assert process is not old_root
        self.roots.append(process)
        await self.record_children()
        self.live = True
        await self.assert_reaped([old_root], old_children)
        await second_prompt(self.client)

    @asynccontextmanager
    async def held_first_spawn(self) -> AsyncIterator[HeldSpawn]:
        await self.close()
        barrier = SpawnBarrier(self.client.spawn_owned_process)
        with patch.object(self.client, "spawn_owned_process", barrier.create):
            starting = self._track(asyncio.create_task(self.client.start()))
            try:
                await barrier.created.wait()
                assert barrier.process is not None
                self.roots.append(barrier.process)
                yield HeldSpawn(
                    starting=starting,
                    root=barrier.process,
                    children=await self.record_children(),
                    barrier=barrier,
                )
            finally:
                barrier.release.set()

    async def cancel_held_start_then_restart(self) -> None:
        async with self.held_first_spawn() as spawn:
            spawn.starting.cancel()
            restarting = await self._queue_restart()
            spawn.release()
            await self._assert_finished(spawn.starting, asyncio.CancelledError)
            await self._claim_restart(restarting, spawn.root, spawn.children)

    async def cancel_held_close_then_restart(self) -> None:
        async with self.held_first_spawn() as spawn:
            closing = await self._queue_close()
            closing.cancel()
            restarting = await self._queue_restart()
            spawn.release()
            await self._assert_finished(spawn.starting, OmpProcessError)
            await self._assert_finished(closing, asyncio.CancelledError)
            await self._claim_restart(restarting, spawn.root, spawn.children)

    async def take(self, step: Step) -> None:
        match step:
            case Step.PROMPT:
                await self.hold_prompt()
            case Step.CANCEL_QUEUED:
                await self.cancel_queued()
            case Step.SETTLE:
                await self.settle()
            case Step.CANCEL_OWNER:
                await self.cancel_owner()
            case Step.CLOSE:
                await self.close()
            case Step.CRASH:
                await self.crash()
            case Step.CANCEL_START_RESTART:
                await self.cancel_held_start_then_restart()
            case Step.CANCEL_CLOSE_RESTART:
                await self.cancel_held_close_then_restart()
        if self.live:
            assert (await self.client.request("checkpoint"))["pid"] is not None

    async def assert_custody_survives(self) -> None:
        """Settle leftover work, reap the tree, and prove a fresh turn still works."""
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
