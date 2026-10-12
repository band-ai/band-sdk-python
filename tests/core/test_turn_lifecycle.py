"""Cancellation coordination preserves cleanup and existing provider deadlines."""

from __future__ import annotations

import asyncio

import pytest

from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS, TurnTask
from band.runtime.cycle import TurnScope


@pytest.mark.parametrize("cooperative", [False, True])
async def test_repeated_termination_preserves_cleanup(cooperative: bool) -> None:
    started, cleaning, release = (asyncio.Event() for _ in range(3))
    outcomes: list[str] = []

    async def work() -> None:
        started.set()
        try:
            if cooperative:
                owner.terminate(cooperative=True)
                raise asyncio.CancelledError
            await asyncio.Future[None]()
        finally:
            cleaning.set()
            await release.wait()
            outcomes.append("cleanup completed")

    owner = TurnTask(asyncio.create_task(work()))
    await started.wait()
    if not cooperative:
        owner.terminate()
    await cleaning.wait()
    owner.terminate()
    owner.terminate()
    release.set()
    assert await owner.drain()
    assert outcomes == ["cleanup completed"]


@pytest.mark.looptime
async def test_expired_grace_retains_cleanup_without_cancelling_again() -> None:
    started, cleaning, release = (asyncio.Event() for _ in range(3))
    outcomes: list[str] = []

    async def work() -> None:
        started.set()
        try:
            await asyncio.Future[None]()
        finally:
            cleaning.set()
            await release.wait()
            outcomes.append("cleanup completed")

    owner = TurnTask(asyncio.create_task(work()))
    await started.wait()
    owner.terminate()
    await cleaning.wait()
    assert not await owner.drain()
    owner.terminate()
    assert not await owner.drain()
    release.set()
    await asyncio.wait({owner.task})
    assert outcomes == ["cleanup completed"]


@pytest.mark.looptime
@pytest.mark.parametrize("timeout_first", [False, True])
async def test_provider_timer_and_stop_share_cleanup(timeout_first: bool) -> None:
    entered, cleaning = asyncio.Event(), asyncio.Event()
    outcomes: list[str] = []

    async def work() -> None:
        async with owner.timed(0.1):
            entered.set()
            try:
                await asyncio.Future[None]()
            finally:
                cleaning.set()
                await asyncio.sleep(0.2)
                outcomes.append("cleanup completed")

    owner = TurnTask(asyncio.create_task(work()))
    await entered.wait()
    if timeout_first:
        await cleaning.wait()
    owner.terminate()
    assert await owner.drain()
    assert owner.task.cancelled()
    assert outcomes == ["cleanup completed"]


@pytest.mark.looptime
async def test_provider_recovery_keeps_its_own_timeout() -> None:
    outcomes: list[str] = []

    async def work() -> None:
        try:
            async with owner.timed(0.1):
                await asyncio.Future[None]()
        except TimeoutError:
            outcomes.append("query timed out")
        try:
            async with owner.timed(0.2, cleanup=True):
                await asyncio.Future[None]()
        except TimeoutError:
            outcomes.append("recovery timed out")

    owner = TurnTask(asyncio.create_task(work()))
    await owner.task
    assert outcomes == ["query timed out", "recovery timed out"]


async def test_released_scope_stays_aborted_after_worker_cleanup() -> None:
    scope = TurnScope(control_revision=0)
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.Future[None]()

    owner = TurnTask(asyncio.create_task(work()))
    scope.bind(owner)
    scope.settled = True
    await started.wait()
    assert not scope.aborted
    owner.terminate()
    assert await owner.drain()
    assert scope.aborted


@pytest.mark.looptime
async def test_registered_worker_inherits_spent_scope_grace() -> None:
    scope = TurnScope(control_revision=0)
    scope.terminate(cooperative=True)
    await asyncio.sleep(CYCLE_CANCEL_GRACE_SECONDS)
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.Future[None]()

    owner = TurnTask(asyncio.create_task(work()))
    await started.wait()
    scope.bind(owner)
    before = asyncio.get_running_loop().time()
    assert await owner.drain()
    assert asyncio.get_running_loop().time() == before
    assert owner.task.cancelled()


@pytest.mark.looptime
async def test_cooperative_worker_without_a_drainer_is_cancelled_at_its_grace() -> None:
    entered = asyncio.Event()

    async def work() -> None:
        async with owner.timed(10 * CYCLE_CANCEL_GRACE_SECONDS):
            entered.set()
            await asyncio.Future[None]()

    owner = TurnTask(asyncio.create_task(work()))
    await entered.wait()
    terminated_at = asyncio.get_running_loop().time()
    owner.terminate(cooperative=True)
    await asyncio.wait({owner.task}, timeout=5 * CYCLE_CANCEL_GRACE_SECONDS)
    assert owner.task.cancelled()
    assert (
        asyncio.get_running_loop().time() - terminated_at == CYCLE_CANCEL_GRACE_SECONDS
    )


def test_finished_work_can_be_drained_after_its_loop_closes() -> None:
    async def finished() -> asyncio.Task[None]:
        async def work() -> None:
            return None

        task = asyncio.create_task(work())
        await task
        return task

    owner = TurnTask(asyncio.run(finished()))
    assert asyncio.run(owner.drain())


async def test_worker_outcomes_are_retrieved_without_raising() -> None:
    """A worker that fails after its message returned was already reported;
    owning it must never raise, even for a cancelled worker."""

    async def failing_turn() -> None:
        raise RuntimeError("boom")

    failed = asyncio.create_task(failing_turn())
    cancelled = asyncio.create_task(asyncio.Event().wait())
    cancelled.cancel()
    await asyncio.wait([failed, cancelled])

    for turn in (failed, cancelled):
        assert await TurnTask(turn).drain()
