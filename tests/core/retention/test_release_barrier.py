"""Recovery joins the residency owner without taking over provider closure."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import ReleaseCause, Residency, RetainedRoom
from tests.adapters.hold import Hold
from tests.core.retention.kit import ResidenceHost


async def join_release(
    room: RetainedRoom, generation: int, entered: asyncio.Event
) -> None:
    entered.set()
    await room.wait_released(generation=generation)


async def hold_lease(room: RetainedRoom, work: Hold) -> None:
    async with room.use():
        await work.wait()


class TestReleaseBarrier:
    async def test_leases_and_two_waiters_share_one_close(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            work, closing = Hold(), Hold()
            resident.closing = closing

            worker = asyncio.create_task(hold_lease(resident.room, work))
            await work.arrived()
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="stop")
            first, second = asyncio.Event(), asyncio.Event()
            one = asyncio.create_task(join_release(resident.room, generation, first))
            two = asyncio.create_task(join_release(resident.room, generation, second))
            await first.wait()
            await second.wait()
            assert not one.done() and not two.done() and resident.releases == []
            work.released.set()
            await worker
            await closing.arrived()
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            assert resident.live and not two.done()
            closing.released.set()
            await two
            assert not resident.live
            assert resident.releases == [ReleaseCause.RECOVERY]
            assert resident.room.snapshot.state is Residency.SUSPENDED

    async def test_final_disposal_wins_while_lease_unwinds(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            work, closing = Hold(), Hold()
            resident.closing = closing

            worker = asyncio.create_task(hold_lease(resident.room, work))
            await work.arrived()
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="stop")
            disposing_started = asyncio.Event()

            async def dispose() -> None:
                disposing_started.set()
                await resident.room.dispose()

            disposing = asyncio.create_task(dispose())
            await disposing_started.wait()
            waiter = asyncio.create_task(
                resident.room.wait_released(generation=generation)
            )
            work.released.set()
            await worker
            await closing.arrived()
            assert not waiter.done()
            closing.released.set()
            await asyncio.gather(waiter, disposing)
            assert resident.releases == [ReleaseCause.FINAL]
            assert resident.room.snapshot.state is Residency.CLOSED

    async def test_release_failure_is_visible_and_retry_owned(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            await resident.turn()
            resident.closing = Hold()
            resident.fail_close = True
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="stop")
            waiter = asyncio.create_task(
                resident.room.wait_released(generation=generation)
            )
            await resident.closing.arrived()
            resident.closing.released.set()
            with pytest.raises(RuntimeError, match="close refused"):
                await waiter
            assert resident.live
            assert resident.room.snapshot.state is Residency.CLEANUP_PENDING
            resident.fail_close = False
            await resident.room.wait_released(generation=generation)
            assert not resident.live
            assert resident.releases == [ReleaseCause.RECOVERY, ReleaseCause.RECOVERY]

    @pytest.mark.looptime
    async def test_waiter_timeout_keeps_close_owned(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            await resident.turn()
            resident.closing = Hold()
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="stop")
            with pytest.raises(CleanupIncompleteError):
                await resident.room.wait_released(generation=generation)
            assert resident.live
            assert resident.releases == [ReleaseCause.RECOVERY]
            resident.closing.released.set()
            await resident.room.wait_released(generation=generation)
            assert not resident.live
            assert resident.releases == [ReleaseCause.RECOVERY]

    async def test_acquisition_cancel_still_joins_physical_release(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            resident.opening, resident.closing = Hold(), Hold()
            worker = asyncio.create_task(resident.turn())
            await resident.opening.arrived()
            generation = resident.room.generation
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
            waiter = asyncio.create_task(
                resident.room.wait_released(generation=generation)
            )
            await resident.closing.arrived()
            assert resident.live and not waiter.done()
            resident.closing.released.set()
            await waiter
            assert not resident.live
            assert resident.releases == [ReleaseCause.RECOVERY]

    @pytest.mark.looptime
    async def test_lease_and_close_share_one_reporting_deadline(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            work, closing = Hold(), Hold()
            resident.closing = closing
            worker = asyncio.create_task(hold_lease(resident.room, work))
            await work.arrived()
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="stop")
            loop = asyncio.get_running_loop()
            budget = host.owner.config.transition_timeout_s
            loop.call_later(budget / 2, work.released.set)
            started = loop.time()
            with pytest.raises(CleanupIncompleteError):
                await resident.room.wait_released(generation=generation)
            assert loop.time() - started == pytest.approx(budget)
            assert resident.releases == [ReleaseCause.RECOVERY]
            assert resident.live
            closing.released.set()
            await asyncio.gather(
                worker, resident.room.wait_released(generation=generation)
            )

    async def test_enrolled_work_cannot_wait_for_its_own_release(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            async with resident.room.use():
                generation = resident.room.generation
                resident.room.recover(generation=generation, reason="stop")
                with pytest.raises(CleanupIncompleteError, match="calling work"):
                    await resident.room.wait_released(generation=generation)
            await resident.room.wait_released(generation=generation)
            assert not resident.live

    async def test_old_generation_join_preserves_the_successor(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            await resident.turn()
            generation = resident.room.generation
            with pytest.raises(ValueError, match="future residency"):
                await resident.room.wait_released(generation=generation + 1)
            with pytest.raises(ValueError, match="no retirement request"):
                await resident.room.wait_released(generation=generation)
            resident.room.recover(generation=generation, reason="stop")
            await resident.room.wait_released(generation=generation)
            await resident.turn()
            await resident.room.wait_released(generation=generation)
            assert resident.live
            assert resident.resume_requests == [False, False]
            assert resident.releases == [ReleaseCause.RECOVERY]
