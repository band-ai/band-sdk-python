"""Acquisition and strict resume of a room's native residency."""

from __future__ import annotations

import asyncio

import pytest

from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestAcquisition:
    """Acquisition resumes strictly and waits for earlier releases."""

    async def test_failed_resume_remains_strict_and_cleans_up_before_retry(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        await host.clock.advance(10)
        await resident.suspended.get()
        resident.fail_acquire = True
        with pytest.raises(RuntimeError, match="resume rejected"):
            await resident.turn()
        resident.fail_acquire = False
        await resident.turn()
        assert resident.resume_requests == [False, True, True]
        assert resident.live

    async def test_successor_waits_for_release_before_lease_and_preparation(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        await host.clock.advance(10)
        await resident.closing.reached.wait()
        prepared: list[int] = []

        async def prepare() -> None:
            prepared.append(resident.room.snapshot.leases)

        async def successor() -> None:
            async with resident.room.use(prepare):
                assert resident.live

        task = asyncio.create_task(successor())
        await asyncio.sleep(0)
        assert resident.room.snapshot.accepted == 1
        assert resident.room.snapshot.leases == 0
        assert prepared == []
        resident.closing.released.set()
        await task
        assert prepared == [1]
        assert resident.resume_requests == [False, True]
