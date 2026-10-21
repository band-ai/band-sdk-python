from __future__ import annotations

import asyncio

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import DetailRecorder

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

    @pytest.mark.looptime
    async def test_a_change_past_the_cap_is_not_reported(self) -> None:
        recorder = DetailRecorder()
        reporter = WorkingStateReporter(
            recorder, keep_alive_seconds=NO_KEEP_ALIVE, max_working_state_seconds=2
        )
        await reporter.start()
        await asyncio.sleep(2.1)
        recorder.detail = "Bash"
        reporter.refresh()
        await asyncio.sleep(1)
        await reporter.stop()
        assert recorder.reports == ["reasoning", None]
