"""Automatic thinking can complete when native state reports an effective level."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.omp.kit import AutoEffectiveRuntime, run_turn


class TestAutomaticThinking:
    @pytest.mark.asyncio
    async def test_auto_thinking_can_complete_with_a_concrete_effective_level(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = AutoEffectiveRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), reasoning_effort="auto"), emit=()
        )
        tools = FakeAgentTools()
        try:
            await run_turn(adapter, tools)
            assert tools.turn.replied
            assert not failure_reports(tools)
            assert runtime.thinking == "auto"
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_rejected_selection_restores_auto_not_effective_level(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = AutoEffectiveRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), reasoning_effort="auto"),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            assert runtime.thinking == "auto"
            with pytest.raises(ValueError, match="reasoning effort"):
                await adapter.apply_model_selection(
                    ModelSelection(reasoning_effort="extreme"),
                    room_id="room-123",
                )
            assert runtime.thinking == "auto"
        finally:
            await adapter.cleanup_all()
