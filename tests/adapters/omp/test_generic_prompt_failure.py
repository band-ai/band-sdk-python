"""A non-timeout prompt crash reports the generic provider failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.custody import occupied_room_ids
from tests.adapters.omp.failures import CrashPromptRuntime
from tests.adapters.omp.turns import run_turn


class TestGenericPromptFailure:
    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_generic_crash_reports_without_a_timeout_code(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = CrashPromptRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        with pytest.raises(RuntimeError, match="provider crashed"):
            await run_turn(adapter, tools)
        failures = reported_failures(tools)
        assert [failure.get("message") for failure in failures] == [
            GENERIC_PROVIDER_FAILURE_MESSAGE
        ]
        assert all(failure.get("code") != "timeout" for failure in failures)
        assert occupied_room_ids(adapter) == set()
        assert runtime.closed
