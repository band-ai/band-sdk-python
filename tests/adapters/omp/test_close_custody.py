from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    FirstCloseFailsRuntime,
    Launches,
    claimed_workspace_rooms,
    launches,
    occupied_room_ids,
    requests_named,
    retained_selection,
    room_runtime,
    run_turn,
)

__all__ = ["launches"]


class TestCloseCustody:
    @pytest.mark.asyncio
    async def test_failed_close_retains_startup_files_and_exclusive_workspace(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        shared = str(tmp_path)
        launches.fail_first_close = True
        adapter = OmpAdapter(workspace_for_room=lambda _room: shared, emit=())
        await run_turn(adapter, FakeAgentTools())
        (runtime,) = launches.runtimes
        (overlay,) = launches.plugins
        try:
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")

            assert room_runtime(adapter, "room-123") is runtime
            assert overlay.exists()
            assert claimed_workspace_rooms(adapter) == ("room-123",)
        finally:
            await adapter.cleanup_all()

        assert runtime.closed
        assert not overlay.exists()
        assert occupied_room_ids(adapter) == set()
        assert claimed_workspace_rooms(adapter) == ()
        reclaimed = adapter._workspaces.claim("other-room")
        assert reclaimed == shared
        adapter._workspaces.release("other-room")

    @pytest.mark.asyncio
    async def test_next_turn_reaps_failed_close_before_starting_a_fresh_runtime(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        launches.fail_first_close = True
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        await run_turn(adapter, FakeAgentTools())
        (retiring,) = launches.runtimes
        try:
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")

            await run_turn(adapter, FakeAgentTools())

            assert retiring.closed
            assert room_runtime(adapter, "room-123") is not retiring
            assert not launches.plugins[0].exists()
            assert launches.plugins[-1].exists()
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_next_turn_applies_selection_retained_after_failed_close(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        adapter, retiring, selection, restore = await _failed_close_with_selection(
            launches, tmp_path
        )
        try:
            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            _assert_fresh_runtime_received_selection(
                launches, adapter, retiring, selection
            )
            assert tools.turn.replied
        finally:
            restore()
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_on_cleanup_retry_applies_selection_retained_after_failed_close(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        adapter, retiring, selection, restore = await _failed_close_with_selection(
            launches, tmp_path
        )
        try:
            await adapter.on_cleanup("room-123")
            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            _assert_fresh_runtime_received_selection(
                launches, adapter, retiring, selection
            )
        finally:
            restore()
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_all_retry_applies_selection_retained_after_failed_close(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        adapter, retiring, selection, restore = await _failed_close_with_selection(
            launches, tmp_path
        )
        try:
            await adapter.cleanup_all()
            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            _assert_fresh_runtime_received_selection(
                launches, adapter, retiring, selection
            )
        finally:
            restore()
            await adapter.cleanup_all()


async def _failed_close_with_selection(launches: Launches, tmp_path: Path):
    launches.fail_first_close = True
    adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
    await run_turn(adapter, FakeAgentTools())
    (retiring,) = launches.runtimes
    assert isinstance(retiring, FirstCloseFailsRuntime)
    selection = ModelSelection(model="provider/replacement", reasoning_effort="auto")
    with pytest.raises(OSError, match="still alive"):
        await adapter.on_cleanup("room-123")
    retiring.reject_requests = True
    await adapter.apply_model_selection(selection, room_id="room-123")
    retiring.reject_requests = False

    def restore() -> None:
        retiring.reject_requests = False

    return adapter, retiring, selection, restore


def _assert_fresh_runtime_received_selection(
    launches: Launches, adapter: OmpAdapter, retiring, selection: ModelSelection
) -> None:
    fresh = launches.runtimes[-1]
    assert retiring.closed
    assert room_runtime(adapter, "room-123") is fresh
    assert retained_selection(adapter, "room-123") == selection
    assert requests_named(fresh, OmpCommandName.SET_MODEL) == [
        {"provider": "provider", "modelId": "replacement"}
    ]
