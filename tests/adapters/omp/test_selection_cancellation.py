"""Interrupted model transactions retire the uncertain native session."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    Launches,
    RecordingRuntime,
    SelectionGateRuntime,
    launches,
    run_turn,
)
from tests.adapters.omp.lifecyclekit import ObservedRoomGate

__all__ = ["launches"]


class TestSelectionCancellation:
    @pytest.mark.parametrize(
        ("held_command", "requested_effort"),
        [
            (OmpCommandName.GET_AVAILABLE_THINKING_LEVELS, "high"),
            (OmpCommandName.SET_THINKING_LEVEL, "extreme"),
        ],
        ids=["during-selection", "during-restoration"],
    )
    @pytest.mark.asyncio
    async def test_interruption_replaces_the_room_with_the_accepted_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        held_command: OmpCommandName,
        requested_effort: str,
    ) -> None:
        interrupted = SelectionGateRuntime()
        fresh = RecordingRuntime()
        interrupted.finish.set()
        fresh.finish.set()
        runtimes = iter((interrupted, fresh))
        monkeypatch.setattr(
            "band.adapters.omp.OmpRpcClient", lambda *a, **kw: next(runtimes)
        )
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path), model="openai/original", reasoning_effort="low"
            ),
            emit=(),
        )
        selection: asyncio.Task[None] | None = None
        try:
            await run_turn(adapter, FakeAgentTools())
            interrupted.held_command = held_command
            selection = asyncio.create_task(
                adapter.apply_model_selection(
                    ModelSelection(
                        model="openai/replacement", reasoning_effort=requested_effort
                    ),
                    room_id="room-123",
                )
            )
            await interrupted.entered.wait()
            selection.cancel()
            with pytest.raises(asyncio.CancelledError):
                await selection

            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            assert interrupted.closed
            assert fresh.model == ("openai", "original")
            assert fresh.thinking == "low"
            assert tools.turn.replied
        finally:
            interrupted.release.set()
            if selection is not None:
                selection.cancel()
                await asyncio.gather(selection, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_turn_queued_during_interrupted_selection_recovers_before_prompting(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        runtime = SelectionGateRuntime()
        launches.runtime_factory = lambda: runtime
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        gate = ObservedRoomGate()
        adapter._locks["room-123"] = gate
        await run_turn(adapter, FakeAgentTools())
        runtime.held_command = OmpCommandName.GET_AVAILABLE_THINKING_LEVELS
        selecting = asyncio.create_task(
            adapter.apply_model_selection(
                ModelSelection(reasoning_effort="high"), room_id="room-123"
            )
        )
        await runtime.entered.wait()
        gate.attempted.clear()
        replay = "[Alice]: context from before the replacement"
        turn = asyncio.create_task(
            run_turn(
                adapter,
                FakeAgentTools(),
                history=OmpSessionState(replay_messages=[replay]),
            )
        )
        try:
            await gate.attempted.wait()
            launches.runtime_factory = RecordingRuntime
            selecting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await selecting
            await turn
            assert runtime.closed and len(launches.runtimes) == 2
            assert launches.runtimes[-1] is not runtime
            assert replay in launches.runtimes[-1].prompt_text
        finally:
            runtime.release.set()
            await asyncio.gather(selecting, turn, return_exceptions=True)
            await adapter.cleanup_all()
