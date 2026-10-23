"""A replacement process is spawned with the deferred room selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import Launches, launches, requests_named, run_turn

__all__ = ["launches"]


class TestReplacementSpawn:
    @pytest.mark.asyncio
    async def test_replacement_process_uses_deferred_room_selection(
        self, launches: Launches, tmp_path: Path
    ):
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path),
                model="openai/original",
                api_key="openai-secret",
            ),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            await adapter.apply_model_selection(
                ModelSelection(model="anthropic/claude", reasoning_effort="low"),
                room_id="room-123",
            )
            (live,) = launches.runtimes
            close = live.close
            live.close = AsyncMock(side_effect=OSError("process still alive"))
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")
            live.close = close
            await run_turn(adapter, FakeAgentTools())
            command, env = launches.commands[-1], launches.envs[-1]
            assert env is not None
            assert "ANTHROPIC_API_KEY" in env
            assert any(part.endswith("anthropic/claude") for part in command)
            assert requests_named(launches.runtimes[-1], OmpCommandName.SET_MODEL)
        finally:
            live.close = close
            await adapter.cleanup_all()
