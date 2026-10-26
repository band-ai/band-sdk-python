"""Close failures on cancel and delivery paths are logged."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.omp.failures import FirstCloseFailsDeliveryRuntime
from tests.adapters.omp.lifecyclekit import FirstCloseFailsRuntime
from tests.adapters.omp.turns import run_turn


class TestCloseFailureLogged:
    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_delivery_failure_logs_a_raising_close(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        runtime = FirstCloseFailsDeliveryRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        try:
            with (
                caplog.at_level(logging.ERROR, logger="band.adapters.omp"),
                pytest.raises(RuntimeError, match="delivery rejected"),
            ):
                await run_turn(adapter, FakeAgentTools())
            assert "failed to close the room after a turn failure" in caplog.text
        finally:
            await adapter.cleanup_all()

    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_cancelled_turn_logs_a_raising_close(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        runtime = FirstCloseFailsRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        try:
            await runtime.yielded.wait()
            with caplog.at_level(logging.ERROR, logger="band.adapters.omp"):
                turn.cancel()
                with pytest.raises(OSError, match="still alive"):
                    await turn
            assert "failed to close the room after a turn failure" in caplog.text
        finally:
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()
