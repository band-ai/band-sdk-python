"""One timer per owner fires idle releases; nothing polls between deadlines."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import RoomRetention, RoomRetentionConfig
from tests.core.retention.kit import ResidenceHost, Resident, armed

pytestmark = pytest.mark.timeout(10)


class TestIdleTimer:
    async def test_the_running_loop_timer_releases_an_idle_room(self) -> None:
        owner = RoomRetention(RoomRetentionConfig(idle_timeout_s=0.01))
        resident = Resident()
        resident.room = owner.register(
            "room",
            acquire=resident.acquire,
            release=resident.release,
            dispose=resident.dispose,
        )
        try:
            await resident.turn()
            await asyncio.wait_for(resident.suspended.get(), timeout=1)
            assert not resident.live
        finally:
            await owner.stop()

    async def test_the_timer_tracks_the_earliest_deadline_and_stop_disarms_it(
        self, host: ResidenceHost
    ) -> None:
        first = host.room("first")
        second = host.room("second")
        await first.turn()
        await host.clock.advance(4)
        await second.turn()
        assert armed(host.clock) == [10]

        await host.owner.stop()
        assert armed(host.clock) == []
