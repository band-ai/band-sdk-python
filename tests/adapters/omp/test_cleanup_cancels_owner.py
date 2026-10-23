"""on_cleanup cancels a live owner task and retires the room."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    PendingRuntime,
    SameTaskCleanupRuntime,
    occupied_room_ids,
    run_turn,
)


class TestCleanupCancelsOwner:
    @pytest.mark.asyncio
    async def test_on_cleanup_cancels_the_live_owner_and_closes(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = PendingRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        await runtime.yielded.wait()
        try:
            await adapter.on_cleanup("room-123")
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert runtime.closed
            assert occupied_room_ids(adapter) == set()
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_same_task_on_cleanup_does_not_cancel_the_occupancy_task(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = SameTaskCleanupRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        runtime.adapter = adapter
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert runtime.cleaned_from_turn
            await adapter.cleanup_all()
            assert runtime.closed
            assert occupied_room_ids(adapter) == set()
        finally:
            await adapter.cleanup_all()
