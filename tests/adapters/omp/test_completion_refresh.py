"""The adapter-phase post-prompt state refresh is completion work too."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations import omprpc
from band.integrations.omprpc import OmpCompletionTimeout
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import run_turn
from tests.integrations.omprpc.kit import OMP_PEER_SCRIPT

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")


@pytest.fixture(autouse=True)
def native_startup_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())


@pytest.fixture
def refresh_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OmpAdapter:
    monkeypatch.setattr(omprpc, "COMPLETION_TIMEOUT_S", 0.25)
    return OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            command=(
                sys.executable,
                "-u",
                OMP_PEER_SCRIPT,
                "--scenario",
                "refresh-state-silent",
            ),
            turn_timeout_s=30.0,
        ),
        emit=(),
    )


class TestPostPromptRefresh:
    async def test_lost_refresh_response_fails_bounded_with_uncertainty(
        self, refresh_adapter: OmpAdapter
    ) -> None:
        # The prompt itself completed honestly (result, settlement, drain); only
        # the adapter-phase refresh goes silent. Before the bound, the turn
        # would sit there holding the room and refreshing activity.
        tools = FakeAgentTools()
        async with asyncio.timeout(10):
            with pytest.raises(OmpCompletionTimeout, match="uncertain"):
                await run_turn(refresh_adapter, tools)
        failures = reported_failures(tools)
        assert failures and failures[0]["provider"] == "omp"
        assert not refresh_adapter._rooms  # the room was retired, not reused
