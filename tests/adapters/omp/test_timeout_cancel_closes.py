"""STOP during timeout failure still retires the room process."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from tests.adapters.omp.kit import (
    CancelDuringFailureTools,
    PendingRuntime,
    occupied_room_ids,
    run_turn,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

TURN_TIMEOUT_S = 1.0


class TestTimeoutCancelCloses:
    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_cancel_during_timeout_send_failure_still_closes(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = PendingRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=TURN_TIMEOUT_S),
            emit=(),
        )
        tools = CancelDuringFailureTools()
        try:
            async with (
                controlled_clock() as clock,
                clock.background(run_turn(adapter, tools)) as turn,
            ):
                await runtime.yielded.wait()
                await clock.elapse(TURN_TIMEOUT_S)
                await tools.entered.wait()
                turn.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await turn
            assert runtime.closed
            assert occupied_room_ids(adapter) == set()
        finally:
            tools.release.set()
            await adapter.cleanup_all()
