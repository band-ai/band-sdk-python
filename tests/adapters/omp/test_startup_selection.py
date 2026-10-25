"""Serialize native model transactions after acquisition finishes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    Launches,
    RecordingRuntime,
    SelectionGateRuntime,
    checkpoint,
    launches,
    run_turn,
)
from tests.adapters.omp.lifecyclekit import ObservedMailbox
from tests.adapters.omp.selectionkit import (
    ACCEPTED_MODEL,
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
)

__all__ = ["launches"]


class TestStartupSelection:
    async def test_selection_overlapping_startup_governs_the_followup_prompt(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        runtime = SelectionGateRuntime()
        runtime.held_command = OmpCommandName.SET_MODEL
        launches.runtime_factory = lambda: runtime
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path), model=f"{MODEL_PROVIDER}/{ORIGINAL_MODEL}"
            ),
            emit=(),
        )
        mailbox = ObservedMailbox()
        adapter._mailbox = mailbox
        first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        await runtime.entered.wait()
        mailbox.attempted.clear()
        selecting = asyncio.create_task(
            adapter.apply_model_selection(
                ModelSelection(model=f"{MODEL_PROVIDER}/{ACCEPTED_MODEL}"),
                room_id="room-123",
            )
        )
        try:
            await mailbox.attempted.wait()
            await checkpoint()
            runtime.held_command = None
            runtime.release.set()
            await selecting
            await first
            await run_turn(adapter, FakeAgentTools(), bootstrap=False)
            assert runtime.models_at_prompt[-1] == (MODEL_PROVIDER, ACCEPTED_MODEL)
        finally:
            runtime.release.set()
            await asyncio.gather(first, selecting, return_exceptions=True)
            await adapter.cleanup_all()

    async def test_accepted_selection_survives_cancelled_startup(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        runtime = SelectionGateRuntime()
        runtime.held_command = OmpCommandName.SET_ASK_DIALOG
        launches.runtime_factory = lambda: runtime
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path), model=f"{MODEL_PROVIDER}/{ORIGINAL_MODEL}"
            ),
            emit=(),
        )
        first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        try:
            await runtime.entered.wait()
            await adapter.apply_model_selection(
                ModelSelection(model=f"{MODEL_PROVIDER}/{ACCEPTED_MODEL}"),
                room_id="room-123",
            )
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert runtime.closed and runtime.models_at_prompt == ()
            launches.runtime_factory = RecordingRuntime
            await run_turn(adapter, FakeAgentTools())
            assert launches.runtimes[-1].models_at_prompt == (
                (MODEL_PROVIDER, ACCEPTED_MODEL),
            )
        finally:
            runtime.release.set()
            await asyncio.gather(first, return_exceptions=True)
            await adapter.cleanup_all()
