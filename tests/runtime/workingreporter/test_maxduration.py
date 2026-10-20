from __future__ import annotations

import asyncio

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import Recorder


class TestMaxDuration:
    @pytest.mark.asyncio
    async def test_keepalive_stops_after_max_but_stop_still_sends_false(self):
        rec = Recorder()
        reporter = WorkingStateReporter(
            rec, keep_alive_seconds=0.02, max_working_state_seconds=0.05
        )

        await reporter.start()
        # Let the cap elapse and the keep-alive go quiet.
        await asyncio.sleep(0.2)
        count_after_cap = rec.calls.count(True)
        await asyncio.sleep(0.1)
        # No further true pings after the cap.
        assert rec.calls.count(True) == count_after_cap

        # stop() still sends the authoritative false.
        await reporter.stop()
        assert rec.calls[-1] is False
