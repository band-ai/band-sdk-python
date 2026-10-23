"""A plugin overlay spawn waits for Band-rule MCP readiness."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import Launches, StartupGate, launches, run_turn

__all__ = ["launches"]


class TestPluginReady:
    @pytest.mark.asyncio
    async def test_spawn_awaits_plugin_ready(
        self, monkeypatch: pytest.MonkeyPatch, launches: Launches, tmp_path: Path
    ) -> None:
        ready = StartupGate()
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        tools = FakeAgentTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        try:
            await ready.entered.wait()
            assert launches.runtimes[0].prompt_text == ""
            assert not turn.done()
            assert not tools.turn.replied
            ready.release.set()
            await turn
            assert tools.turn.replied
        finally:
            ready.release.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()
