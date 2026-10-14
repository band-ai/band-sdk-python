"""Idle deadlines: when a quiescent room is released, and what postpones it."""

from __future__ import annotations

import pytest

from band.core.retention import (
    ProviderActivity,
    Residency,
)
from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestIdleScheduling:
    """Idle deadlines: when a quiescent room is released, and what postpones it."""

    async def test_idle_rooms_release_independently_of_other_room_traffic(
        self,
        host: ResidenceHost,
    ) -> None:
        idle = host.room("idle")
        busy = host.room("busy")
        await idle.turn()
        await busy.turn()
        await host.clock.advance(6)
        await busy.turn()
        await host.clock.advance(4)
        await idle.suspended.get()
        assert not idle.live
        assert busy.live
        await host.clock.advance(6)
        await busy.suspended.get()
        assert not busy.live

    @pytest.mark.parametrize(
        "activity", [ProviderActivity.ACTIVE, ProviderActivity.UNKNOWN]
    )
    async def test_provider_completion_rearms_without_another_message(
        self, host: ResidenceHost, activity: ProviderActivity
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.observe(activity, "background work remains unaccounted for")
        await host.clock.advance(100)
        assert resident.live
        assert resident.room.snapshot.deadline is None
        resident.observe(ProviderActivity.QUIESCENT)
        await host.clock.advance(10)
        await resident.suspended.get()
        assert not resident.live

    async def test_actual_work_lease_pins_a_quiescent_provider(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        async with resident.room.use():
            await host.clock.advance(100)
            assert resident.live
            assert resident.room.snapshot.deadline is None
        await host.clock.advance(10)
        await resident.suspended.get()
        assert not resident.live

    async def test_repeated_quiescence_does_not_postpone_release(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        await host.clock.advance(6)
        resident.observe(ProviderActivity.QUIESCENT)
        await host.clock.advance(4)
        await resident.suspended.get()
        assert not resident.live

    async def test_native_activity_restarts_the_whole_idle_interval(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        await host.clock.advance(6)
        resident.observe(ProviderActivity.ACTIVE)
        resident.observe(ProviderActivity.QUIESCENT)
        await host.clock.advance(4)
        assert resident.live
        await host.clock.advance(6)
        await resident.suspended.get()
        assert not resident.live

    async def test_slow_close_does_not_block_another_room(
        self, host: ResidenceHost
    ) -> None:
        slow = host.room("slow")
        fast = host.room("fast")
        await slow.turn()
        await fast.turn()
        slow.closing = Barrier()
        await host.clock.advance(10)
        await slow.closing.reached.wait()
        await fast.suspended.get()
        await fast.turn()
        assert slow.live and fast.live
        assert len(fast.resume_requests) == 2

    async def test_busy_idle_refusal_keeps_the_intact_residency(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.busy = True
        await host.clock.advance(10)
        await resident.suspended.get()
        snapshot = resident.room.snapshot
        assert snapshot.state is Residency.RESIDENT
        assert snapshot.provider_activity is ProviderActivity.ACTIVE
        assert not snapshot.pending_cleanup
        await resident.turn()
        resident.busy = False
        resident.observe(ProviderActivity.QUIESCENT)
        await host.clock.advance(10)
        await resident.suspended.get()
        assert not resident.live
        assert resident.resume_requests == [False]
