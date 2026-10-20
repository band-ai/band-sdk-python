"""An owned task's failure is logged once: by its joiner, or here when unjoined."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.core.retention.cleanup import report_owned, spawn_owned
from tests.adapters.hold import Hold
from tests.adapters.waiting import spin, until
from tests.core.retention.kit import (
    RETENTION_LOGGER,
    ResidenceHost,
    retention_logs,
    retention_records,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock

pytestmark = pytest.mark.timeout(10)


@pytest.fixture(autouse=True)
def capture_retention(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=RETENTION_LOGGER)


class TestFailureLogging:
    """A failure a caller re-raises is left to that caller's log."""

    async def test_a_joined_acquire_failure_is_left_to_its_caller(
        self, host: ResidenceHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        resident = host.room()
        resident.fail_acquire = True
        with pytest.raises(RuntimeError, match="resume rejected"):
            await resident.turn()
        await resident.suspended.get()
        assert retention_logs(caplog.records) == []

    async def test_a_failed_shutdown_is_left_to_the_stopping_caller(
        self, host: ResidenceHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        with pytest.raises(BaseExceptionGroup):
            await host.owner.stop()
        assert retention_logs(caplog.records) == []

    async def test_an_unjoined_idle_release_failure_is_logged_once(
        self, host: ResidenceHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        await host.clock.advance(10)
        await until(
            lambda: bool(retention_logs(caplog.records)),
            failure="the idle release failure was never logged",
        )
        (record,) = retention_records(caplog.records)
        assert (record.levelno, record.getMessage()) == (
            logging.WARNING,
            "Room room residency transition failed: close refused",
        )
        assert record.exc_info is not None

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_failure_raised_as_its_wait_times_out_is_left_to_its_caller(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        loop = asyncio.get_running_loop()
        gate: asyncio.Future[None] = loop.create_future()
        order: list[str] = []

        async def failing_cleanup() -> None:
            await gate
            order.append("cleanup failed")
            raise RuntimeError("close failed")

        async with (
            controlled_clock() as clock,
            background(spawn_owned(failing_cleanup(), label="cleanup")) as cleanup,
        ):
            cleanup.add_done_callback(lambda _: order.append("done callback"))
            release = loop.call_later(1, gate.set_result, None)
            # Both distinct timers become due in one step; equal deadlines have
            # no guaranteed callback order in asyncio's timer heap.
            jump = loop.call_soon(clock.advance, 3)
            try:
                with pytest.raises(RuntimeError, match="close failed"):
                    try:
                        await report_owned(cleanup, 2)
                    finally:
                        order.append("caller stopped waiting")
                await spin()
                assert order == [
                    "cleanup failed",
                    "caller stopped waiting",
                    "done callback",
                ]
                assert retention_logs(caplog.records) == []
            finally:
                release.cancel()
                jump.cancel()

    async def test_a_failure_its_cancelled_caller_never_saw_is_logged_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The task fails, then its caller is cancelled in the same loop step,
        so the done callback runs while that caller still counts as reporting."""
        gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def failing_cleanup() -> None:
            await gate
            raise RuntimeError("close failed")

        cleanup = spawn_owned(failing_cleanup(), label="cleanup")
        caller = asyncio.create_task(report_owned(cleanup, None))
        await spin()
        gate.set_result(None)
        asyncio.get_running_loop().call_soon(caller.cancel)
        with pytest.raises(asyncio.CancelledError):
            await caller
        await spin()
        assert retention_logs(caplog.records) == [
            (logging.WARNING, "cleanup failed: close failed")
        ]

    async def test_an_abandoned_start_cancelled_for_recovery_is_logged(
        self, impatient_host: ResidenceHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        resident = impatient_host.room()
        resident.opening = Hold()
        with pytest.raises(TimeoutError, match="start abandoned"):
            await resident.turn()
        assert retention_logs(caplog.records) == [
            (
                logging.WARNING,
                (
                    "Room room: abandoned acquisition (generation 1) "
                    "cancelled for recovery"
                ),
            )
        ]
