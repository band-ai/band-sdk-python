"""Cancelled native startup retains its overlay and workspace through settlement."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    Launches,
    RecordingRuntime,
    claimed_workspace_rooms,
    launches,
    run_turn,
    start_cleanup,
)
from tests.adapters.omp.lifecyclekit import SettlingStartRuntime

__all__ = ["launches"]


class TestAcquisitionSettlement:
    @pytest.mark.parametrize("clear_cancel", [False, True])
    @pytest.mark.asyncio
    async def test_cleanup_joins_startup_settlement_without_recancelling(
        self, launches: Launches, tmp_path: Path, clear_cancel: bool
    ) -> None:
        runtime = SettlingStartRuntime(clear_cancel=clear_cancel)
        launches.runtime_factory = lambda: runtime
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        try:
            await runtime.starting.arrived()
            turn.cancel()
            await runtime.settling.arrived()
            cleanup = await start_cleanup(adapter)
            assert not cleanup.done() and not runtime.closed
            assert runtime.cancellations == 1
            assert launches.plugins[0].exists()
            assert claimed_workspace_rooms(adapter) == ("room-123",)
            runtime.settling.released.set()
            await cleanup
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert runtime.cancellations == 1 and runtime.closed
            assert not launches.plugins[0].exists()
            assert claimed_workspace_rooms(adapter) == ()
            launches.runtime_factory = RecordingRuntime
            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            assert tools.turn.replied and len(launches.runtimes) == 2
        finally:
            runtime.starting.released.set()
            runtime.settling.released.set()
            await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()
