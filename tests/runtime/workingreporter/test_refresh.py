from __future__ import annotations

import asyncio

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import DetailRecorder, Projection

# Longer than any test, so every report after start is a refresh.
NO_KEEP_ALIVE = 3600.0


class TestRefresh:
    async def test_a_change_is_reported_without_waiting_for_the_keep_alive(
        self,
    ) -> None:
        recorder = DetailRecorder()
        reporter = WorkingStateReporter(recorder, keep_alive_seconds=NO_KEEP_ALIVE)
        await reporter.start()
        recorder.detail = "Bash"
        reporter.refresh()
        assert await recorder.reports_reach(2) == ["reasoning", "Bash"]
        await reporter.stop()
        assert recorder.reports == ["reasoning", "Bash", None]

    async def test_a_change_during_the_first_report_follows_it(self) -> None:
        recorder = DetailRecorder()
        reporter = WorkingStateReporter(recorder, keep_alive_seconds=NO_KEEP_ALIVE)
        recorder.hold()
        starting = asyncio.create_task(reporter.start())
        await recorder.entered.wait()
        recorder.detail = "Bash"
        reporter.refresh()
        recorder.release()
        await starting
        assert await recorder.reports_reach(2) == ["reasoning", "Bash"]
        await reporter.stop()

    async def test_changes_during_a_report_coalesce_to_the_latest(self) -> None:
        recorder = DetailRecorder()
        reporter = WorkingStateReporter(recorder, keep_alive_seconds=NO_KEEP_ALIVE)
        await reporter.start()
        recorder.hold()
        recorder.detail = "Read"
        reporter.refresh()
        await recorder.entered.wait()
        recorder.detail = "Bash"
        reporter.refresh()
        recorder.detail = "memory"
        reporter.refresh()
        recorder.release()
        assert await recorder.reports_reach(3) == ["reasoning", "Read", "memory"]
        await reporter.stop()
        assert recorder.reports == ["reasoning", "Read", "memory", None]

    async def test_a_change_after_revocation_is_cleared_without_a_true_report(
        self,
    ) -> None:
        recorder = DetailRecorder()
        projection = Projection()
        reporter = WorkingStateReporter(
            recorder, keep_alive_seconds=NO_KEEP_ALIVE, project=projection
        )
        await reporter.start()
        projection.change(False)
        recorder.detail = "Bash"
        reporter.refresh()
        await projection.sampled.wait()
        await reporter.stop()
        assert recorder.reports == ["reasoning", None]
