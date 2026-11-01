"""The sender pauses and wakes according to canonical execution eligibility."""

from __future__ import annotations

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import DetailRecorder, Projection


class TestReportingEligibility:
    async def test_capped_work_pauses_and_fresh_work_wakes_the_same_sender(
        self,
    ) -> None:
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
            projection.change(True)
            recorder.detail = "fresh"
            reporter.refresh()
            assert await recorder.reports_reach(2) == ["reasoning", "fresh"]
        finally:
            await reporter.stop()
        assert recorder.reports == ["reasoning", "fresh", None]
