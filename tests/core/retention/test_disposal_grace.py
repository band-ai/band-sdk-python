"""Final disposal closes admission at once, then lets accepted work finish
within the owner's settle grace before cancelling what remains."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.core.retention import ReleaseCause, Residency
from tests.core.retention.kit import (
    SETTLE_GRACE_S,
    Barrier,
    ResidenceHost,
    Resident,
    armed,
)

pytestmark = pytest.mark.timeout(10)


async def parked_work(resident: Resident, barrier: Barrier) -> asyncio.Task[None]:
    """Owned work leasing the room until ``barrier`` is released."""

    async def work() -> None:
        async with resident.room.use():
            await barrier.wait()

    task = asyncio.create_task(work())
    resident.room.own(task)
    await barrier.reached.wait()
    return task


# Loop turns a disposal may take to reach its grace; a bound, not a delay.
GRACE_TURNS = 20


async def grace_started(host: ResidenceHost, rooms: int = 1) -> None:
    """Let a started disposal or shutdown arm ``rooms`` settle graces."""
    for _ in range(GRACE_TURNS):
        if len(armed(host.clock)) >= rooms:
            return
        await asyncio.sleep(0)
    raise AssertionError("no settle grace was armed")


async def leaving(host: ResidenceHost, resident: Resident) -> asyncio.Task[None]:
    """A leave whose disposal is inside its settle grace."""
    disposal = asyncio.create_task(resident.room.dispose())
    await grace_started(host)
    return disposal


class TestDisposalGrace:
    async def test_work_arriving_during_the_grace_is_refused(
        self, graceful_host: ResidenceHost
    ) -> None:
        resident = graceful_host.room()
        parked = Barrier()
        work = await parked_work(resident, parked)
        disposal = await leaving(graceful_host, resident)

        late = asyncio.create_task(resident.turn())
        with pytest.raises(RoomClosingError):
            resident.room.own(late)

        assert resident.room.closing and not work.done()
        parked.released.set()
        await disposal

    async def test_work_finishing_within_the_grace_is_never_cancelled(
        self, graceful_host: ResidenceHost
    ) -> None:
        resident = graceful_host.room()
        parked = Barrier()
        work = await parked_work(resident, parked)
        disposal = await leaving(graceful_host, resident)

        await graceful_host.clock.advance(SETTLE_GRACE_S - 1)
        assert not work.done() and resident.releases == []
        parked.released.set()
        await disposal

        assert not work.cancelled()
        assert resident.releases == [ReleaseCause.FINAL]
        assert resident.room.snapshot.state is Residency.CLOSED

    async def test_work_outlasting_the_grace_is_cancelled_once(
        self, graceful_host: ResidenceHost
    ) -> None:
        resident = graceful_host.room()
        work = await parked_work(resident, Barrier())
        disposal = await leaving(graceful_host, resident)

        await graceful_host.clock.advance(SETTLE_GRACE_S)
        await disposal

        assert work.cancelled()
        assert resident.releases == [ReleaseCause.FINAL]

    async def test_a_report_timeout_in_the_grace_leaves_one_joinable_disposal(
        self, graceful_impatient_host: ResidenceHost
    ) -> None:
        resident = graceful_impatient_host.room()
        parked = Barrier()
        work = await parked_work(resident, parked)

        with pytest.raises(CleanupIncompleteError):
            await resident.room.dispose()
        assert not work.done() and resident.live

        parked.released.set()
        await work
        await resident.room.dispose()

        assert not work.cancelled()
        assert resident.releases == [ReleaseCause.FINAL]

    async def test_a_cancelled_leave_leaves_its_disposal_owned(
        self, graceful_host: ResidenceHost
    ) -> None:
        resident = graceful_host.room()
        parked = Barrier()
        await parked_work(resident, parked)
        disposal = await leaving(graceful_host, resident)

        disposal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await disposal
        parked.released.set()
        await resident.room.dispose()

        assert resident.releases == [ReleaseCause.FINAL]
        assert resident.room.snapshot.state is Residency.CLOSED

    async def test_a_retry_after_a_failed_release_closes_at_once(
        self, graceful_host: ResidenceHost
    ) -> None:
        resident = graceful_host.room()
        await resident.turn()
        resident.fail_close = True
        work = await parked_work(resident, Barrier())
        disposal = await leaving(graceful_host, resident)
        await graceful_host.clock.advance(SETTLE_GRACE_S)
        with pytest.raises(RuntimeError, match="close refused"):
            await disposal
        assert work.cancelled()

        resident.fail_close = False
        await resident.room.dispose()

        assert resident.releases == [ReleaseCause.FINAL, ReleaseCause.FINAL]
        assert resident.room.snapshot.state is Residency.CLOSED

    async def test_shutdown_gives_every_room_the_same_grace(
        self, graceful_host: ResidenceHost
    ) -> None:
        first, second = graceful_host.room("first"), graceful_host.room("second")
        works = [
            await parked_work(first, Barrier()),
            await parked_work(second, Barrier()),
        ]
        stopping = asyncio.create_task(graceful_host.owner.stop())
        await grace_started(graceful_host, rooms=2)

        await graceful_host.clock.advance(SETTLE_GRACE_S - 1)
        assert not any(work.done() for work in works)
        await graceful_host.clock.advance(1)
        await stopping

        assert all(work.cancelled() for work in works)

    async def test_work_requesting_its_own_leave_is_spared_through_the_grace(
        self, graceful_host: ResidenceHost
    ) -> None:
        resident = graceful_host.room()
        unwinding = Barrier()

        async def leave_from_inside() -> None:
            async with resident.room.use():
                with pytest.raises(CleanupIncompleteError):
                    await resident.room.dispose()
                await unwinding.wait()

        work = asyncio.create_task(leave_from_inside())
        resident.room.own(work)
        await unwinding.reached.wait()
        await graceful_host.clock.advance(SETTLE_GRACE_S)
        await asyncio.sleep(0)
        assert not work.done()

        unwinding.released.set()
        await work
        await resident.room.dispose()

        assert resident.releases == [ReleaseCause.FINAL]
