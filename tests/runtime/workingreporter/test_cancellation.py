from __future__ import annotations

import asyncio

import pytest

from band.runtime.working_state import WorkingStateReporter
from tests.runtime.workingreporter.kit import Recorder, _wait_for


class TestCancellation:
    @pytest.mark.asyncio
    async def test_false_sent_even_when_cycle_cancelled(self):
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)

        async def cycle():
            await reporter.start()
            try:
                await asyncio.sleep(100)  # long-running reasoning
            finally:
                await reporter.stop()

        task = asyncio.create_task(cycle())
        await _wait_for(lambda: rec.calls == [True])

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The shielded false-send must have completed despite the cancel.
        await _wait_for(lambda: rec.calls and rec.calls[-1] is False)
        assert rec.calls.count(False) == 1

    def test_stop_sends_false_when_the_keepalive_loop_has_closed(self) -> None:
        rec = Recorder()
        reporter = WorkingStateReporter(rec, keep_alive_seconds=10.0)
        with asyncio.Runner() as runner:
            runner.run(reporter.start())

        resumed = asyncio.new_event_loop()
        try:
            resumed.run_until_complete(reporter.stop())
        finally:
            resumed.close()

        assert rec.calls == [True, False]
