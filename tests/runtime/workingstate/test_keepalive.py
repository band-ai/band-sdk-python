from __future__ import annotations

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingstate.kit import Recorder, _wait_for


class TestKeepAlive:
    @pytest.mark.asyncio
    async def test_refreshes_true_on_cadence(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=0.02)

        await reporter.start()
        # initial true + at least two refreshes
        await _wait_for(lambda: rec.calls.count(True) >= 3)
        await reporter.stop()

        assert rec.calls.count(True) >= 3
        assert rec.calls[-1] is False

    @pytest.mark.asyncio
    async def test_reentrant_start_does_not_duplicate_keepalive(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)

        await reporter.start()
        await reporter.start()  # already active -> no-op

        assert rec.calls == [True]  # only one initial true, one task
        await reporter.stop()
