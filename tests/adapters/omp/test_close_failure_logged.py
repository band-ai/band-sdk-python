"""Close failures on cancel and delivery paths are logged."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.omp.failures import BoomCloseDeliveryRuntime
from tests.adapters.omp.lifecyclekit import BoomCloseRuntime
from tests.adapters.omp.turns import run_turn


class TestCloseFailureLogged:
    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_delivery_failure_logs_a_raising_close(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        runtime = BoomCloseDeliveryRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        with (
            caplog.at_level(logging.ERROR, logger="band.adapters.omp"),
            pytest.raises(RuntimeError, match="delivery rejected"),
        ):
            await run_turn(adapter, FakeAgentTools())
        assert "failed to close the room after a turn failure" in caplog.text

    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_cancelled_turn_logs_a_raising_close(
        self, monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        runtime = BoomCloseRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        await runtime.yielded.wait()
        with caplog.at_level(logging.ERROR, logger="band.adapters.omp"):
            turn.cancel()
            with pytest.raises(OSError, match="still alive"):
                await turn
        assert "failed to close the room after a turn failure" in caplog.text
