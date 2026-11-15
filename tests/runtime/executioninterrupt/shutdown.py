"""Owned work and generated actions for room shutdown tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS, TurnTask
from band.runtime.execution import ExecutionContext
from tests.adaptercleanup import CleanupPlatform
from tests.adapters.hold import Hold
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import auxiliary_context
from tests.runtime.helpers import rest_client_over
from tests.toolkit.timecontrol import ControlledClock, controlled_clock

WORK_ID = "work"


class FinalizingHandler:
    """Keep finalization open and acknowledge every delivered cancellation."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finalization = Hold()
        self.finished = asyncio.Event()
        self.cancellations: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self, ctx: ExecutionContext, event: object) -> None:
        self.started.set()
        try:
            await self.finalization.released.wait()
        except asyncio.CancelledError:
            self.cancellations.put_nowait(None)
            raise
        finally:
            self.finalization.reached.set()
            while not self.finalization.released.is_set():
                try:
                    await self.finalization.released.wait()
                except asyncio.CancelledError:
                    self.cancellations.put_nowait(None)
            self.finished.set()


class ObservedTurnTask(TurnTask):
    def __init__(self, task: asyncio.Task[None]) -> None:
        super().__init__(task)
        self.draining = asyncio.Event()

    async def drain(self) -> bool:
        self.draining.set()
        return await super().drain()


@asynccontextmanager
async def managed_execution(
    ctx: ExecutionContext, *, release: asyncio.Event | None = None
) -> AsyncIterator[ExecutionContext]:
    """Own shutdown before startup or event admission can fail."""
    try:
        yield ctx
    finally:
        if release is not None:
            release.set()
        await ctx.stop()


class ShutdownStep(StrEnum):
    STOP = "stop"
    JOIN_STOP = "concurrent stop"
    SETTLE = "settle accepted work"


class ShutdownScenario:
    def __init__(
        self, ctx: ExecutionContext, clock: ControlledClock, handler: FinalizingHandler
    ) -> None:
        self.ctx = ctx
        self.clock = clock
        self.handler = handler
        self.expected_cancellations = 0
        self.settled = False
        self.stops: list[asyncio.Task[bool]] = []

    @property
    def work(self) -> asyncio.Task[None]:
        scope = self.ctx.current_scope
        assert scope is not None and scope.handler is not None
        return scope.handler.task

    async def take(self, step: ShutdownStep) -> None:
        match step:
            case ShutdownStep.SETTLE:
                await self.settle()
            case ShutdownStep.STOP:
                await self.stop(callers=1)
            case ShutdownStep.JOIN_STOP:
                await self.stop(callers=2)

    async def settle(self) -> None:
        self.handler.finalization.released.set()
        await asyncio.gather(self.work, return_exceptions=True)
        self.settled = True
        assert self.handler.finished.is_set()

    async def stop(self, *, callers: int) -> None:
        attempts = [asyncio.create_task(self.ctx.stop()) for _ in range(callers)]
        self.stops.extend(attempts)
        if self.settled:
            assert all(await asyncio.gather(*attempts))
            return
        await self.handler.cancellations.get()
        self.expected_cancellations += 1
        await self.clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
        self.assert_retained()
        for stopping in attempts:
            with pytest.raises(CleanupIncompleteError):
                await stopping

    def assert_retained(self) -> None:
        assert self.work.cancelling() == self.expected_cancellations
        assert not self.work.done()
        assert not self.handler.finished.is_set()
        assert not self.ctx.is_running

    async def close(self) -> None:
        self.handler.finalization.released.set()
        await asyncio.gather(*self.stops, return_exceptions=True)
        await self.ctx.stop()
        if self.handler.started.is_set():
            assert self.handler.finished.is_set()


@asynccontextmanager
async def shutdown_scenario() -> AsyncIterator[ShutdownScenario]:
    platform = CleanupPlatform()
    platform.add_message(WORK_ID)
    async with controlled_clock() as clock, rest_client_over(platform.answer) as rest:
        handler = FinalizingHandler()
        ctx = auxiliary_context(rest, handler)
        scenario = ShutdownScenario(ctx, clock, handler)
        try:
            await ctx.start()
            await ctx.on_event(make_message_event(msg_id=WORK_ID))
            await handler.started.wait()
            yield scenario
        finally:
            await scenario.close()
