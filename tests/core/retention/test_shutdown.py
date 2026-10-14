"""Owner shutdown attempts every room and keeps failures owned."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import RoomClosingError
from band.core.retention import (
    ReleaseCause,
)
from tests.adapters.waiting import spin
from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestShutdown:
    """Owner shutdown attempts every room and keeps failures owned."""

    async def test_shutdown_attempts_every_room_and_retries_failed_ownership(
        self,
        host: ResidenceHost,
    ) -> None:
        failed = host.room("failed")
        other = host.room("other")
        await failed.turn()
        await other.turn()
        failed.fail_dispose = True
        with pytest.raises(ExceptionGroup):
            await host.owner.stop()
        assert failed.identity is not None and other.identity is None
        assert failed.room.snapshot.pending_cleanup
        with pytest.raises(RuntimeError):
            await failed.turn()
        failed.fail_dispose = False
        await host.owner.stop()
        assert failed.identity is None
        assert failed.releases == [ReleaseCause.FINAL]

    async def test_cancelled_shutdown_keeps_cleanup_owned_and_blocks_wake(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.leaving = Barrier()
        stopping = asyncio.create_task(host.owner.stop())
        await resident.leaving.reached.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        with pytest.raises(RuntimeError):
            await resident.turn()
        resident.leaving.released.set()
        await host.owner.stop()
        assert resident.identity is None

    async def test_shutdown_preempts_queued_provider_acquisition(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        wake = asyncio.create_task(resident.turn())
        stopping = asyncio.create_task(host.owner.stop())
        with pytest.raises(RoomClosingError):
            await wake
        await stopping
        assert resident.resume_requests == []
        assert not resident.live

    async def test_concurrent_stops_and_a_leave_join_one_close(
        self,
        host: ResidenceHost,
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Barrier()
        callers = [
            asyncio.create_task(host.owner.stop()),
            asyncio.create_task(host.owner.stop()),
            asyncio.create_task(resident.room.dispose()),
        ]
        await resident.closing.reached.wait()
        await spin()
        assert not any(caller.done() for caller in callers)
        resident.closing.released.set()
        await asyncio.gather(*callers)
        assert resident.releases == [ReleaseCause.FINAL]
        assert resident.identity is None
