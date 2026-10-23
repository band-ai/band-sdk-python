"""A partial selection keeps the previously accepted live model on spawn."""

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


class TestPartialSelectionSpawn:
    @pytest.mark.asyncio
    async def test_effort_only_request_keeps_the_live_model_on_replacement(
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
            await adapter.apply_model_selection(
                ModelSelection(reasoning_effort="high"),
                room_id="room-123",
            )
            (live,) = launches.runtimes
            close = live.close
            live.close = AsyncMock(side_effect=OSError("process still alive"))
            with pytest.raises(OSError, match="still alive"):
                await adapter.on_cleanup("room-123")
            live.close = close
            await run_turn(adapter, FakeAgentTools())
            command = launches.commands[-1]
            fresh = launches.runtimes[-1]
            assert any(part.endswith("anthropic/claude") for part in command)
            assert not any(part.endswith("openai/original") for part in command)
            assert requests_named(fresh, OmpCommandName.SET_MODEL)[-1] == {
                "provider": "anthropic",
                "modelId": "claude",
            }
            assert requests_named(fresh, OmpCommandName.SET_THINKING_LEVEL)[-1] == {
                "level": "high"
            }
        finally:
            live.close = close
            await adapter.cleanup_all()
