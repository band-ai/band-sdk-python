"""Native OMP timeout cleanup settles processes before reporting failure."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import PendingRuntime, run_turn


class TestProcessCleanup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("during_setup", [False, True])
    async def test_timeout_retires_process_before_reporting_failure(
        self, monkeypatch, tmp_path: Path, during_setup: bool
    ):
        runtime = PendingRuntime()
        if during_setup:
            runtime.start = AsyncMock(side_effect=asyncio.Event().wait)
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=0.01), emit=()
        )
        tools = FakeAgentTools()
        with pytest.raises(TimeoutError):
            await run_turn(adapter, tools)
        assert runtime.closed
        assert not adapter._rooms
        assert [
            (failure["provider"], failure.get("code"))
            for failure in reported_failures(tools)
        ] == [("omp", "timeout")]
        assert not tools.turn.replied
