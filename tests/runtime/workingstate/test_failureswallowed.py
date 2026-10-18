from __future__ import annotations

import asyncio

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingstate.kit import Recorder


class TestFailureSwallowed:
    @pytest.mark.asyncio
    async def test_report_exception_does_not_propagate(self):
        rec = Recorder(fail=True)
        reporter = WorkingStateReporter(rec, keep_alive_seconds=0.02)

        # Neither start nor the keep-alive task nor stop may raise.
        await reporter.start()
        await asyncio.sleep(0.05)
        await reporter.stop()

        assert rec.calls  # attempts were made
