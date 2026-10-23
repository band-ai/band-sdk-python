"""Physical release consumes selection clearing before logical disposal."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompprocess import OmpMcpServer
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    Launches,
    RecordingRuntime,
    launches,
    retained_selection,
    run_turn,
)
from tests.adapters.omp.lifecyclekit import HeldCloseRuntime, ObservedMailbox
from tests.adapters.omp.selectionkit import ACCEPTED_MODEL, MODEL_PROVIDER

__all__ = ["launches"]


class TestSelectionFinalization:
    @pytest.mark.asyncio
    async def test_selection_queued_during_close_survives_logical_disposal(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        runtime = HeldCloseRuntime()
        launches.runtime_factory = lambda: runtime
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        gate = ObservedMailbox()
        adapter._mailbox = gate
        await run_turn(adapter, FakeAgentTools())
        cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
        await runtime.closing.arrived()
        gate.attempted.clear()
        selection = ModelSelection(model=f"{MODEL_PROVIDER}/{ACCEPTED_MODEL}")
        selecting = asyncio.create_task(
            adapter.apply_model_selection(selection, room_id="room-123")
        )
        try:
            await gate.attempted.wait()
            runtime.closing.released.set()
            await selecting
            await cleanup
            assert runtime.closed
            assert retained_selection(adapter, "room-123") == selection
            launches.runtime_factory = RecordingRuntime
            await run_turn(adapter, FakeAgentTools())
            assert launches.runtimes[-1].models_at_prompt == (
                (MODEL_PROVIDER, ACCEPTED_MODEL),
            )
        finally:
            runtime.closing.released.set()
            await asyncio.gather(selecting, cleanup, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_resolver_selection_is_applied_before_the_first_prompt(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        selection = ModelSelection(model=f"{MODEL_PROVIDER}/{ACCEPTED_MODEL}")

        async def resolve(workspace: str) -> dict[str, OmpMcpServer]:
            await adapter.apply_model_selection(selection, room_id="room-123")
            return {}

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
        )
        try:
            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            assert launches.runtimes[0].models_at_prompt == (
                (MODEL_PROVIDER, ACCEPTED_MODEL),
            )
            assert tools.turn.replied
        finally:
            await adapter.cleanup_all()
