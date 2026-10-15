"""Owner shutdown attempts every room and keeps failures owned."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.core.retention import (
    ReleaseCause,
)
from tests.adapters.hold import Hold
from tests.adapters.waiting import spin
from tests.core.retention.kit import RETENTION_LOGGER, ResidenceHost, retention_logs

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
        resident.leaving = Hold()
        stopping = asyncio.create_task(host.owner.stop())
        await resident.leaving.arrived()
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
        resident.closing = Hold()
        callers = [
            asyncio.create_task(host.owner.stop()),
            asyncio.create_task(host.owner.stop()),
            asyncio.create_task(resident.room.dispose()),
        ]
        await resident.closing.arrived()
        await spin()
        assert not any(caller.done() for caller in callers)
        resident.closing.released.set()
        await asyncio.gather(*callers)
        assert resident.releases == [ReleaseCause.FINAL]
        assert resident.identity is None

    async def test_stops_that_stopped_waiting_leave_one_shutdown_failure(
        self,
        impatient_host: ResidenceHost,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Every caller gives up before the held disposal fails, so only the
        shutdown they all joined is left to log that failure."""
        caplog.set_level(logging.WARNING, logger=RETENTION_LOGGER)
        resident = impatient_host.room()
        await resident.turn()
        resident.leaving = Hold()
        resident.fail_dispose = True
        callers = [
            asyncio.create_task(impatient_host.owner.stop()),
            asyncio.create_task(impatient_host.owner.stop()),
            asyncio.create_task(resident.room.dispose()),
        ]
        outcomes = await asyncio.gather(*callers, return_exceptions=True)
        assert [type(outcome) for outcome in outcomes] == [CleanupIncompleteError] * 3
        resident.leaving.released.set()
        await spin()
        assert retention_logs(caplog.records) == [
            (
                logging.WARNING,
                (
                    "Room retention shutdown failed: "
                    "Room cleanup remains incomplete (1 sub-exception)"
                ),
            )
        ]

    async def test_owned_shutdown_can_wait_beyond_the_callers_reporting_budget(
        self, impatient_host: ResidenceHost
    ) -> None:
        resident = impatient_host.room()
        await resident.turn()
        resident.leaving = Hold()
        stopping = asyncio.create_task(impatient_host.owner.stop(report_timeout_s=None))
        try:
            await asyncio.wait(
                {stopping}, timeout=2 * impatient_host.owner.config.transition_timeout_s
            )
            if stopping.done():
                await stopping
                pytest.fail("owned shutdown returned before disposal settled")
            assert resident.identity is not None
        finally:
            resident.leaving.released.set()
        await stopping
        assert resident.identity is None
