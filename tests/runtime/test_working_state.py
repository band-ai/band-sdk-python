"""Tests for WorkingStateReporter (boolean working-state keep-alive)."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.working_state import WorkingStateReporter


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll until predicate() is truthy or raise on timeout (non-brittle wait)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


class Recorder:
    """Async report callback that records the sequence of working values."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[bool] = []
        self.fail = fail

    async def __call__(self, working: bool) -> bool:
        self.calls.append(working)
        if self.fail:
            raise RuntimeError("report failed")
        return True


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


class TestValidation:
    def test_non_positive_cadence_rejected(self):
        with pytest.raises(ValueError):
            WorkingStateReporter(Recorder(), keep_alive_seconds=0)
