"""A raising close still reports the turn failure that triggered it."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import FirstCloseFailsRuntime, run_turn
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

TURN_TIMEOUT_S = 1.0


class TestCloseReportsFailure:
    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_timeout_still_reports_when_close_raises(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = FirstCloseFailsRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=TURN_TIMEOUT_S), emit=()
        )
        tools = FakeAgentTools()
        try:
            async with (
                controlled_clock() as clock,
                clock.background(run_turn(adapter, tools)) as turn,
            ):
                await runtime.yielded.wait()
                await clock.elapse(TURN_TIMEOUT_S)
                with pytest.raises(OSError, match="still alive"):
                    await turn
            assert any(
                failure.get("code") == "timeout" for failure in reported_failures(tools)
            )
        finally:
            await adapter.cleanup_all()
