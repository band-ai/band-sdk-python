"""Final disposal keeps unfinished cleanup owned."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    Residency,
)
from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestDisposal:
    """Final disposal keeps unfinished cleanup owned and retryable."""

    async def test_leave_wins_against_wake_during_close(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        await host.clock.advance(10)
        await resident.closing.reached.wait()
        leaving = asyncio.create_task(resident.room.dispose())
        wake = asyncio.create_task(resident.turn())
        resident.closing.released.set()
        await leaving
        with pytest.raises(RuntimeError):
            await wake
        assert resident.releases == [ReleaseCause.IDLE, ReleaseCause.FINAL]
        assert len(resident.resume_requests) == 1
        assert resident.room.snapshot.state is Residency.CLOSED
        assert not resident.live

    async def test_a_failed_dispose_after_an_idle_release_retries_final(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        resident.leaving = Barrier()
        await host.clock.advance(10)
        await resident.closing.reached.wait()
        leaving = asyncio.create_task(resident.room.dispose())
        await asyncio.sleep(0)
        resident.closing.released.set()
        await resident.leaving.reached.wait()
        resident.fail_dispose = True
        resident.leaving.released.set()
        with pytest.raises(RuntimeError, match="dispose refused"):
            await leaving
        assert resident.releases == [ReleaseCause.IDLE, ReleaseCause.FINAL]
        assert resident.identity == "native-1"

        resident.fail_dispose = False
        await resident.room.dispose()
        assert resident.releases == [ReleaseCause.IDLE, ReleaseCause.FINAL]
        assert resident.identity is None

    async def test_cancelled_disposal_waiter_leaves_retryable_owned_cleanup(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.leaving = Barrier()
        leaving = asyncio.create_task(resident.room.dispose())
        await resident.leaving.reached.wait()
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        resident.leaving.released.set()
        await resident.room.dispose()
        assert resident.releases == [ReleaseCause.FINAL]
        assert resident.identity is None
        assert resident.room.snapshot.state is Residency.CLOSED

    async def test_failed_disposal_retains_room_registration(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_dispose = True
        with pytest.raises(RuntimeError, match="dispose refused"):
            await resident.room.dispose()
        with pytest.raises((RuntimeError, ValueError)):
            host.room()
        resident.fail_dispose = False
        await resident.room.dispose()
        replacement = host.room()
        await replacement.turn()
        assert replacement.live and not resident.live
        assert not resident.observe(ProviderActivity.QUIESCENT)

    async def test_disabled_retention_still_disposes_owned_resources(self) -> None:
        host = ResidenceHost(timeout=None)
        try:
            resident = host.room()
            await resident.turn()
            assert resident.room.snapshot.deadline is None
            await host.owner.stop()
            assert not resident.live
        finally:
            await host.close()

    async def test_disposal_report_timeout_is_joined_without_a_second_close(
        self,
        impatient_host: ResidenceHost,
    ) -> None:
        resident = impatient_host.room()
        await resident.turn()
        resident.closing = Barrier()
        with pytest.raises(CleanupIncompleteError):
            await resident.room.dispose()
        assert resident.room.snapshot.state is Residency.SUSPENDING
        assert resident.room.snapshot.pending_release is ReleaseCause.FINAL
        resident.closing.released.set()
        await resident.room.dispose()
        assert resident.releases == [ReleaseCause.FINAL]

    @pytest.mark.parametrize("failure", ["release", "dispose"])
    async def test_failed_final_cleanup_retries_the_same_handle(
        self, host: ResidenceHost, failure: str
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = failure == "release"
        resident.fail_dispose = failure == "dispose"
        with pytest.raises(RuntimeError, match="refused"):
            await resident.room.dispose()
        assert resident.room.snapshot.pending_cleanup
        assert resident.identity == "native-1"
        resident.fail_close = resident.fail_dispose = False
        await resident.room.dispose()
        expected = 2 if failure == "release" else 1
        assert resident.releases == [ReleaseCause.FINAL] * expected
        assert resident.identity is None

    async def test_an_operation_waiting_on_a_lease_learns_of_disposal_at_once(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        holding = asyncio.Event()
        finish = asyncio.Event()

        async def hold_a_lease() -> None:
            async with resident.room.use():
                holding.set()
                await finish.wait()

        holder = asyncio.create_task(hold_a_lease())
        await holding.wait()
        resident.room.recover(
            generation=resident.room.snapshot.generation, reason="crashed"
        )
        waiter = asyncio.create_task(resident.turn())
        await asyncio.sleep(0)

        disposal = asyncio.create_task(resident.room.dispose())
        with pytest.raises(RuntimeError, match="is closing"):
            await asyncio.wait_for(waiter, timeout=1)
        assert not holder.done()

        finish.set()
        await holder
        await disposal
