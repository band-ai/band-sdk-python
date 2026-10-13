"""Failed or abandoned acquisitions go through recovery."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    Residency,
)
from tests.core.retention.kit import Barrier, ResidenceHost, Resident

pytestmark = pytest.mark.timeout(10)


async def hung_start(resident: Resident) -> tuple[asyncio.Task[None], Barrier]:
    """A turn waiting at a start's held opening; later starts don't wait."""
    opening = Barrier()
    resident.opening = opening
    wake = asyncio.create_task(resident.turn())
    await opening.reached.wait()
    resident.opening = None
    return wake, opening


class TestRecovery:
    """A failed or abandoned acquisition is recovered, never idled."""

    async def test_abandoned_startup_is_recovered_before_reuse(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        resident.opening = Barrier()
        wake = asyncio.create_task(resident.turn())
        await resident.opening.reached.wait()
        wake.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wake
        resident.opening.released.set()
        await resident.suspended.get()
        await resident.turn()
        assert resident.releases == [ReleaseCause.RECOVERY]
        assert resident.resume_requests == [False, False]
        assert resident.identity == "native-1"
        assert resident.live

    async def test_partial_acquisition_keeps_handle_until_recovery_succeeds(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        resident.fail_acquire = resident.fail_close = True
        with pytest.raises(RuntimeError, match="resume rejected"):
            await resident.turn()
        await resident.suspended.get()
        with pytest.raises(RuntimeError, match="close refused"):
            await resident.turn()
        assert resident.live
        assert len(resident.resume_requests) == 1
        assert resident.room.snapshot.pending_release is ReleaseCause.RECOVERY
        resident.fail_close = resident.fail_acquire = False
        await resident.turn()
        assert resident.live
        assert len(resident.resume_requests) == 2
        assert set(resident.releases) == {ReleaseCause.RECOVERY}

    async def test_failed_acquisition_is_recovered_never_idled(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        resident.startup_activity = ProviderActivity.ACTIVE
        resident.fail_acquire = True
        resident.closing = Barrier()
        with pytest.raises(RuntimeError, match="resume rejected"):
            await resident.turn()
        await resident.closing.reached.wait()
        assert resident.releases == [ReleaseCause.RECOVERY]
        resident.closing.released.set()
        await resident.suspended.get()
        await host.clock.advance(100)
        assert resident.releases == [ReleaseCause.RECOVERY]

    async def test_identity_created_before_startup_failure_survives_recovery(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        resident.fail_acquire = True
        with pytest.raises(RuntimeError, match="resume rejected"):
            await resident.turn()
        resident.fail_acquire = False
        await resident.turn()
        assert resident.identity == "native-1"
        assert resident.room.snapshot.generation == 2

    async def test_recovery_waits_for_the_reporting_operation_to_unwind(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        successors: list[asyncio.Task[None]] = []

        async def crashing_turn() -> None:
            async with resident.room.use():
                generation = resident.room.snapshot.generation
                assert resident.room.recover(generation=generation, reason="exited")
                assert not resident.room.observe(
                    ProviderActivity.QUIESCENT, generation=generation
                )
                successors.append(asyncio.create_task(resident.turn()))
                await asyncio.sleep(0)
                snapshot = resident.room.snapshot
                assert snapshot.state is Residency.CLEANUP_PENDING
                assert (snapshot.leases, snapshot.accepted) == (1, 2)

        await crashing_turn()
        await successors[0]
        assert resident.releases == [ReleaseCause.RECOVERY]
        assert resident.resume_requests == [False, False]
        assert resident.room.snapshot.generation == 2

    async def test_acquisition_report_timeout_keeps_it_owned_for_recovery(
        self,
        impatient_host: ResidenceHost,
    ) -> None:
        resident = impatient_host.room()
        resident.opening = Barrier()
        with pytest.raises(TimeoutError):
            await resident.turn()
        assert resident.room.snapshot.state is Residency.ACQUIRING
        resident.opening.released.set()
        await resident.suspended.get()
        await resident.turn()
        assert resident.releases == [ReleaseCause.RECOVERY]
        assert resident.resume_requests == [False, False]

    @pytest.mark.parametrize("stopped", [True, False], ids=["stopped", "timed-out"])
    async def test_an_abandoned_start_that_never_finishes_is_recovered(
        self, impatient_host: ResidenceHost, stopped: bool
    ) -> None:
        resident = impatient_host.room()
        wake, _ = await hung_start(resident)
        if stopped:
            wake.cancel()
        with pytest.raises(asyncio.CancelledError if stopped else TimeoutError):
            await wake
        await resident.suspended.get()
        await resident.turn()
        assert resident.releases == [ReleaseCause.RECOVERY]
        assert resident.room.snapshot.generation == 2

    async def test_an_operation_joining_an_abandoned_start_is_served(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        wake, _ = await hung_start(resident)
        joiner = asyncio.create_task(resident.turn())
        await asyncio.sleep(0)
        assert resident.room.snapshot.accepted == 2
        wake.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wake
        await joiner
        assert resident.releases == [ReleaseCause.RECOVERY]
        assert resident.room.snapshot.generation == 2

    async def test_a_start_resisting_cancellation_is_not_closed_while_it_runs(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        resident.resists_cancel = True
        wake, opening = await hung_start(resident)
        wake.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wake
        await asyncio.sleep(0)  # the start takes its cancellation and keeps going
        assert resident.releases == []
        assert resident.room.snapshot.state is Residency.ACQUIRING
        opening.released.set()
        await resident.suspended.get()
        await resident.turn()
        assert resident.releases == [ReleaseCause.RECOVERY]
        assert resident.room.snapshot.generation == 2

    async def test_recovery_during_an_idle_release_is_not_a_strict_resume(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        await host.clock.advance(10)
        await resident.closing.reached.wait()
        generation = resident.room.snapshot.generation
        assert resident.room.recover(generation=generation, reason="crashed") is True
        resident.closing.released.set()
        await resident.suspended.get()
        await asyncio.sleep(0)
        await resident.turn()
        assert resident.releases == [ReleaseCause.IDLE, ReleaseCause.RECOVERY]
        assert resident.resume_requests == [False, False]
