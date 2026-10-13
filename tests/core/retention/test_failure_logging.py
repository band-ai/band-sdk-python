"""An owned task's failure is logged once: by its joiner, or here when unjoined."""

from __future__ import annotations

import logging

import pytest

from tests.adapters.waiting import until
from tests.core.retention.kit import (
    RETENTION_LOGGER,
    Barrier,
    ResidenceHost,
    retention_logs,
)

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
        assert retention_logs(caplog.records) == [
            (logging.WARNING, "Room room residency transition failed: close refused")
        ]
        assert caplog.records[0].exc_info is not None

    async def test_an_abandoned_start_cancelled_for_recovery_is_logged(
        self, impatient_host: ResidenceHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        resident = impatient_host.room()
        resident.opening = Barrier()
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
