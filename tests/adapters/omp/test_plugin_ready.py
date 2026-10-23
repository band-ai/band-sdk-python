"""A plugin overlay spawn waits for Band-rule MCP readiness."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import launches, run_turn

__all__ = ["launches"]


class TestPluginReady:
    @pytest.mark.asyncio
    async def test_spawn_awaits_plugin_ready(
        self, launches, native_startup_ready: AsyncMock, tmp_path: Path
    ) -> None:
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        try:
            await run_turn(adapter, FakeAgentTools())
            native_startup_ready.assert_awaited()
        finally:
            await adapter.cleanup_all()
