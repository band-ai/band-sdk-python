from __future__ import annotations

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import Recorder


class TestOrdering:
    @pytest.mark.asyncio
    async def test_false_always_after_true_even_on_ultra_short_cycle(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)

        # Ultra-short cycle: start then immediately stop.
        await reporter.start()
        await reporter.stop()

        # The lock must guarantee true is sent (and lands) before false.
        assert rec.calls[0] is True
        assert rec.calls[-1] is False
        # No false appears before the first true.
        first_false = rec.calls.index(False)
        first_true = rec.calls.index(True)
        assert first_true < first_false
