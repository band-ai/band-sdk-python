"""A silent native selection gate neither holds its caller nor the room."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import retained_selection
from tests.adapters.omp.lifecyclekit import Launches
from tests.adapters.omp.models import RecordingRuntime, SelectionGateRuntime
from tests.adapters.omp.resolution import cross_deadline
from tests.adapters.omp.turns import run_turn
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

BUDGET_S = 0.2
PAST_BUDGET_S = 0.4


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestSelectionGateBudget:
    @pytest.mark.asyncio
    async def test_silent_gate_times_out_the_caller_and_recovery_admits_a_new_turn(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        silent = SelectionGateRuntime()
        launches.runtime_factory = lambda: silent
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=BUDGET_S), emit=()
        )
        selection: asyncio.Task[None] | None = None
        try:
            async with controlled_clock() as clock:
                await run_turn(adapter, FakeAgentTools())
                silent.held_command = OmpCommandName.GET_AVAILABLE_THINKING_LEVELS
                selection = asyncio.create_task(
                    adapter.apply_model_selection(
                        ModelSelection(reasoning_effort="high"), room_id="room-123"
                    )
                )
                await silent.entered.wait()

                await cross_deadline(clock, PAST_BUDGET_S)

                with pytest.raises(TimeoutError, match="room is recovering"):
                    await selection
                # The gate never answered: the room, not the gate, was released.
                assert not silent.release.is_set()
                assert retained_selection(adapter, "room-123") is None

                launches.runtime_factory = RecordingRuntime
                tools = FakeAgentTools()
                await run_turn(adapter, tools)

                assert silent.closed and len(launches.runtimes) == 2
                assert launches.runtimes[-1] is not silent
                assert tools.turn.replied
        finally:
            silent.release.set()
            if selection is not None:
                selection.cancel()
                await asyncio.gather(selection, return_exceptions=True)
            await adapter.cleanup_all()
