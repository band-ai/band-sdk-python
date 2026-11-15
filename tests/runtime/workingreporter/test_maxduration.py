"""The sender pauses and wakes according to canonical execution eligibility."""

from __future__ import annotations

import logging

import pytest

from band.runtime.working_state import WORKING_CAP_PAUSED, WorkingStateReporter
from tests.runtime.workingreporter.kit import DetailRecorder, Projection


class TestReportingEligibility:
    @pytest.mark.looptime
    async def test_capped_work_pauses_and_fresh_work_wakes_the_same_sender(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=WorkingStateReporter.__module__)
        pause_log = (WorkingStateReporter.__module__, logging.INFO, WORKING_CAP_PAUSED)
        recorder = DetailRecorder()
        projection = Projection()
        reporter = WorkingStateReporter(
            recorder, project=projection, keep_alive_seconds=3600
        )
        try:
            await reporter.start()
            projection.change(None)
            reporter.refresh()
            await projection.sampled.wait()
            assert caplog.record_tuples == [pause_log]
            projection.change(None)
            reporter.refresh()
            await projection.sampled.wait()
            assert caplog.record_tuples == [pause_log]
            projection.change(True)
            recorder.detail = "fresh"
            reporter.refresh()
            assert await recorder.reports_reach(2) == ["reasoning", "fresh"]
            projection.change(None)
            reporter.refresh()
            await projection.sampled.wait()
            assert caplog.record_tuples == [pause_log, pause_log]
        finally:
            await reporter.stop()
        assert recorder.reports == ["reasoning", "fresh", None]
