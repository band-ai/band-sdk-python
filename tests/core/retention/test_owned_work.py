"""Accepted detached work blocks idle release and is settled by disposal."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.core.retention import (
    ReleaseCause,
    Residency,
)
from tests.conftest import eager_tasks, requires_eager_tasks
from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestOwnedWork:
    """Accepted detached work blocks idle release and is settled by disposal."""

    async def test_owned_queued_work_revokes_idle_until_it_actually_finishes(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        queued = Barrier()

        async def detached_turn() -> None:
            await queued.wait()
            await resident.turn()

        task = asyncio.create_task(detached_turn())
        resident.room.own(task)
        assert resident.room.snapshot.accepted == 1
        assert resident.room.snapshot.deadline is None
        await host.clock.advance(100)
        assert resident.live and not resident.releases
        queued.released.set()
        await task
        assert resident.room.snapshot.accepted == 0
        await host.clock.advance(10)
        await resident.suspended.get()
        assert resident.releases == [ReleaseCause.IDLE]

    async def test_queued_work_cancelled_before_use_releases_ownership(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        task = asyncio.create_task(resident.turn())
        resident.room.own(task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert resident.room.snapshot.accepted == 0
        await host.clock.advance(10)
        await resident.suspended.get()
        await resident.room.dispose()
        assert resident.room.snapshot.state is Residency.CLOSED

    async def test_final_cleanup_cancels_and_joins_owned_work(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        parked = Barrier()

        async def parked_decision() -> None:
            async with resident.room.use():
                await parked.wait()

        task = asyncio.create_task(parked_decision())
        resident.room.own(task)
        await parked.reached.wait()
        await resident.room.dispose()
        assert task.cancelled()
        assert resident.releases == [ReleaseCause.FINAL]
        rejected = asyncio.create_task(resident.turn())
        with pytest.raises(RuntimeError, match="closing"):
            resident.room.own(rejected)
        with pytest.raises(asyncio.CancelledError):
            await rejected
        assert rejected.cancelled()

    async def test_cancellation_resistant_work_keeps_disposal_pending(
        self,
        impatient_host: ResidenceHost,
    ) -> None:
        resident = impatient_host.room()
        await resident.turn()
        finish = asyncio.Event()

        async def stubborn() -> None:
            async with resident.room.use():
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await finish.wait()

        task = asyncio.create_task(stubborn())
        resident.room.own(task)
        await asyncio.sleep(0)
        with pytest.raises(CleanupIncompleteError):
            await resident.room.dispose()
        assert resident.live and not resident.releases
        assert resident.room.snapshot.pending_cleanup
        finish.set()
        await task
        await resident.room.dispose()
        assert resident.releases == [ReleaseCause.FINAL]

    async def test_enrolled_work_cannot_await_its_own_disposal(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()

        async def leave_from_inside() -> None:
            async with resident.room.use():
                with pytest.raises(CleanupIncompleteError):
                    await resident.room.dispose()
                assert resident.live

        await leave_from_inside()
        await resident.room.dispose()
        assert resident.room.snapshot.state is Residency.CLOSED
        assert resident.releases == [ReleaseCause.FINAL]

    @pytest.mark.parametrize("cleanup", ["dispose", "stop"])
    async def test_owned_task_requesting_cleanup_unwinds_without_cancellation(
        self, host: ResidenceHost, cleanup: str
    ) -> None:
        resident = host.room()
        await resident.turn()
        requested, unwinding = asyncio.Event(), asyncio.Event()

        async def leave_from_owned_work() -> None:
            async with resident.room.use():
                with pytest.raises(CleanupIncompleteError):
                    if cleanup == "dispose":
                        await resident.room.dispose()
                    else:
                        await host.owner.stop()
                requested.set()
                await unwinding.wait()

        task = asyncio.create_task(leave_from_owned_work())
        resident.room.own(task)
        await requested.wait()
        assert task.cancelling() == 0
        assert resident.releases == []
        unwinding.set()
        await task
        await resident.room.dispose()
        assert resident.releases == [ReleaseCause.FINAL]

    @requires_eager_tasks
    async def test_started_work_runs_only_once_enrolled(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        enrolled_when_run: list[bool] = []

        async def work() -> None:
            current = asyncio.current_task()
            enrolled_when_run.append(resident.room.enrolls(current))

        with eager_tasks():
            task = resident.room.start_owned(work)
        await task

        assert enrolled_when_run == [True]

    @requires_eager_tasks
    async def test_work_started_on_a_closing_room_never_runs(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        disposal = asyncio.create_task(resident.room.dispose())
        await resident.closing.reached.wait()
        ran: list[bool] = []

        async def work() -> None:
            ran.append(True)

        with eager_tasks(), pytest.raises(RoomClosingError):
            resident.room.start_owned(work)
        resident.closing.released.set()
        await disposal

        assert ran == []
