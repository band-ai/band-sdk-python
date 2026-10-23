"""Rejected native model selections leave the live session on its prior model."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import RecordingRuntime, run_turn


class TestModelSelection:
    @pytest.mark.asyncio
    async def test_rejected_effort_restores_the_running_model(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path),
                model="openai/original",
                reasoning_effort="low",
            ),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            with pytest.raises(ValueError, match="reasoning effort"):
                await adapter.apply_model_selection(
                    ModelSelection(
                        model="openai/replacement", reasoning_effort="extreme"
                    ),
                    room_id="room-123",
                )

            assert runtime.model == ("openai", "original")
            assert runtime.thinking == "low"
            assert adapter._room_selections == {}
        finally:
            await adapter.cleanup_all()
