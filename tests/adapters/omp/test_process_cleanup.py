"""Native OMP timeout cleanup settles processes before reporting failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import HungStartRuntime, PendingRuntime, run_turn
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

TURN_TIMEOUT_S = 1.0


class TestProcessCleanup:
    @pytest.mark.asyncio
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_prompt_timeout_retires_process_before_reporting_failure(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = PendingRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=TURN_TIMEOUT_S), emit=()
        )
        tools = FakeAgentTools()
        async with (
            controlled_clock() as clock,
            clock.background(run_turn(adapter, tools)) as turn,
        ):
            await runtime.yielded.wait()
            assert not runtime.closed
            await clock.elapse(TURN_TIMEOUT_S)
            with pytest.raises(TimeoutError):
                await turn
        assert runtime.closed
        assert not adapter._rooms
        assert [
            (failure["provider"], failure.get("code"))
            for failure in reported_failures(tools)
        ] == [("omp", "timeout")]
        assert not tools.turn.replied

    @pytest.mark.asyncio
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_setup_timeout_retires_process_before_reporting_failure(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = HungStartRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=TURN_TIMEOUT_S), emit=()
        )
        tools = FakeAgentTools()
        async with (
            controlled_clock() as clock,
            clock.background(run_turn(adapter, tools)) as turn,
        ):
            await runtime.started.wait()
            assert not runtime.closed
            await clock.elapse(TURN_TIMEOUT_S)
            with pytest.raises(TimeoutError):
                await turn
        assert runtime.closed
        assert not adapter._rooms
        assert [
            (failure["provider"], failure.get("code"))
            for failure in reported_failures(tools)
        ] == [("omp", "timeout")]
        assert not tools.turn.replied
