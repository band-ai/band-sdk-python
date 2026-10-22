from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter
from band.core.model_catalog import ModelSelection
from band.integrations.omprpc import OmpProcessError
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import Launches, launches, run_turn

__all__ = ["launches"]


class TestCloseCustody:
    @pytest.mark.asyncio
    async def test_failed_close_retains_startup_files_and_exclusive_workspace(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        shared = str(tmp_path)
        adapter = OmpAdapter(workspace_for_room=lambda _room: shared, emit=())
        await run_turn(adapter, FakeAgentTools())
        (runtime,) = launches.runtimes
        (overlay,) = launches.plugins
        close = runtime.close
        runtime.close = AsyncMock(side_effect=OSError("process still alive"))
        try:
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")

            assert adapter._rooms["room-123"].runtime is runtime
            assert overlay.exists()
            with pytest.raises(ValueError):
                adapter._workspaces.claim("other-room")
        finally:
            runtime.close = close
            await adapter.cleanup_all()

        assert runtime.closed
        assert not overlay.exists()
        assert not adapter._rooms
        assert not adapter._workspaces.rooms

    @pytest.mark.asyncio
    async def test_next_turn_reaps_failed_close_before_starting_a_fresh_runtime(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        await run_turn(adapter, FakeAgentTools())
        (retiring,) = launches.runtimes
        close = retiring.close
        retiring.close = AsyncMock(side_effect=OSError("process still alive"))
        try:
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")
            retiring.close = close

            await run_turn(adapter, FakeAgentTools())

            assert retiring.closed
            assert adapter._rooms["room-123"].runtime is not retiring
            assert not launches.plugins[0].exists()
            assert launches.plugins[-1].exists()
        finally:
            retiring.close = close
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cleanup_retry", ["next_turn", "on_cleanup", "cleanup_all"]
    )
    async def test_selection_after_failed_close_is_retained_for_the_next_turn(
        self, launches: Launches, tmp_path: Path, cleanup_retry: str
    ) -> None:
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        await run_turn(adapter, FakeAgentTools())
        (retiring,) = launches.runtimes
        close, request = retiring.close, retiring.request
        retiring.close = AsyncMock(side_effect=OSError("process still alive"))
        selection = ModelSelection(
            model="provider/replacement", reasoning_effort="auto"
        )
        try:
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")
            retiring.request = AsyncMock(
                side_effect=OmpProcessError("client is closing")
            )

            await adapter.apply_model_selection(selection, room_id="room-123")
            retiring.close = close
            if cleanup_retry == "on_cleanup":
                await adapter.on_cleanup("room-123")
            elif cleanup_retry == "cleanup_all":
                await adapter.cleanup_all()
            tools = FakeAgentTools()
            await run_turn(adapter, tools)

            assert tools.turn.replied
            assert retiring.closed
            assert adapter._rooms["room-123"].runtime is not retiring
            assert adapter._room_selections["room-123"] == selection
        finally:
            retiring.close, retiring.request = close, request
            await adapter.cleanup_all()
