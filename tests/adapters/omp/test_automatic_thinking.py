"""Automatic thinking can complete when native state reports an effective level."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.omp.custody import owned_room
from tests.adapters.omp.modelpeer import NativeFault
from tests.adapters.omp.runtime import AdapterFactory
from tests.adapters.omp.turns import run_turn
from tests.integrations.omprpc.wirelog import sent_command_parameters


class TestAutomaticThinking:
    @pytest.mark.asyncio
    async def test_auto_thinking_can_complete_with_a_concrete_effective_level(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ):
        adapter = real_adapter(
            faults=(NativeFault.EFFECTIVE_AUTO,),
            config=OmpAdapterConfig(cwd=str(tmp_path), reasoning_effort="auto"),
            emit=(),
        )
        tools = FakeAgentTools()
        await run_turn(adapter, tools)
        assert tools.turn.replied
        assert not failure_reports(tools)
        commands = await sent_command_parameters(
            owned_room(adapter, "room-123").client, OmpCommandName.SET_THINKING_LEVEL
        )
        assert commands[-1] == {"level": "auto"}

    @pytest.mark.asyncio
    async def test_rejected_selection_restores_auto_not_effective_level(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ):
        adapter = real_adapter(
            faults=(NativeFault.EFFECTIVE_AUTO,),
            config=OmpAdapterConfig(cwd=str(tmp_path), reasoning_effort="auto"),
            emit=(),
        )
        await run_turn(adapter, FakeAgentTools())
        commands = await sent_command_parameters(
            owned_room(adapter, "room-123").client, OmpCommandName.SET_THINKING_LEVEL
        )
        assert commands[-1] == {"level": "auto"}
        with pytest.raises(ValueError, match="reasoning effort"):
            await adapter.apply_model_selection(
                ModelSelection(reasoning_effort="extreme"),
                room_id="room-123",
            )
        commands = await sent_command_parameters(
            owned_room(adapter, "room-123").client, OmpCommandName.SET_THINKING_LEVEL
        )
        assert commands[-1] == {"level": "auto"}
