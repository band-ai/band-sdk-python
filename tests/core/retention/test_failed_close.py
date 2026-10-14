"""A failed or interrupted idle close stays owned and retries safely."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
)
from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestFailedClose:
    """A failed or interrupted idle close stays owned and retries safely."""

    async def test_failed_close_retries_on_deadline_without_a_message(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await resident.suspended.get()
        assert resident.live
        assert resident.room.snapshot.pending_cleanup
        await host.clock.advance(9)
        assert resident.suspended.empty()
        resident.fail_close = False
        await host.clock.advance(1)
        await resident.suspended.get()
        assert not resident.live
        assert not resident.room.snapshot.pending_cleanup

    async def test_failed_close_cannot_create_a_replacement(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await resident.suspended.get()
        with pytest.raises(RuntimeError, match="close refused"):
            await resident.turn()
        assert len(resident.resume_requests) == 1
        assert resident.live

    @pytest.mark.parametrize(
        "activity", [ProviderActivity.ACTIVE, ProviderActivity.UNKNOWN]
    )
    async def test_new_native_work_prevents_retrying_a_failed_idle_close(
        self, host: ResidenceHost, activity: ProviderActivity
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await resident.suspended.get()
        resident.fail_close = False
        resident.observe(activity)
        with pytest.raises(RuntimeError):
            await resident.turn()
        await host.clock.advance(100)
        assert resident.live
        assert resident.suspended.empty()
        resident.observe(ProviderActivity.QUIESCENT)
        await host.clock.advance(10)
        await resident.suspended.get()
        assert not resident.live

    async def test_cancelled_wake_waiter_does_not_cancel_close_or_duplicate_acquisition(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        await host.clock.advance(10)
        await resident.closing.reached.wait()
        admitted = asyncio.Event()

        async def wake_room() -> None:
            admitted.set()
            await resident.turn()

        wake = asyncio.create_task(wake_room())
        await admitted.wait()
        wake.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wake
        resident.closing.released.set()
        await asyncio.gather(resident.turn(), resident.turn())
        assert len(resident.resume_requests) == 2
        assert resident.resume_requests[-1] is True
        assert resident.live

    async def test_native_activity_queued_before_retry_close_prevents_release(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await resident.suspended.get()
        resident.fail_close = False
        wake = asyncio.create_task(resident.turn())
        asyncio.get_running_loop().call_soon(resident.observe, ProviderActivity.ACTIVE)
        with pytest.raises(RuntimeError, match="cleanup pending"):
            await wake
        assert resident.live
        assert len(resident.resume_requests) == 1
        assert resident.suspended.empty()

    async def test_failed_idle_close_retries_before_waiting_successor_leases(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await resident.suspended.get()
        resident.fail_close = False
        await resident.turn()
        assert resident.releases == [ReleaseCause.IDLE, ReleaseCause.IDLE]
        assert resident.resume_requests == [False, True]
