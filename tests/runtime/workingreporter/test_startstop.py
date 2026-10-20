from __future__ import annotations

import asyncio

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import Recorder


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_sends_true_immediately(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)

        await reporter.start()

        assert rec.calls == [True]
        await reporter.stop()

    @pytest.mark.asyncio
    async def test_stop_sends_false(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)

        await reporter.start()
        await reporter.stop()

        assert rec.calls[0] is True
        assert rec.calls[-1] is False
        assert rec.calls.count(False) == 1

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)

        await reporter.stop()

        assert rec.calls == []

    @pytest.mark.asyncio
    async def test_disabled_reports_nothing(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=0.01, enabled=False)

        await reporter.start()
        await asyncio.sleep(0.05)
        await reporter.stop()

        assert rec.calls == []
