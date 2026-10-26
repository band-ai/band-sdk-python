"""Native model selection applies, restores the last selector, and fail-closes."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import owned_room, retained_selection
from tests.adapters.omp.modelpeer import FAIL_MODEL_RESTORE, NativeFault
from tests.adapters.omp.runtime import AdapterFactory
from tests.adapters.omp.turns import run_turn
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.integrations.omprpc.wirelog import sent_command_parameters


class TestModelSelection:
    @pytest.mark.asyncio
    async def test_rejected_effort_restores_the_running_model(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        adapter = real_adapter(
            config=OmpAdapterConfig(
                cwd=str(tmp_path),
                model="openai/original",
                reasoning_effort="low",
            ),
            emit=(),
        )
        await run_turn(adapter, FakeAgentTools())
        with pytest.raises(ValueError, match="reasoning effort"):
            await adapter.apply_model_selection(
                ModelSelection(model="openai/replacement", reasoning_effort="extreme"),
                room_id="room-123",
            )

        client = owned_room(adapter, "room-123").client
        state = await client.request(OmpCommandName.GET_STATE)
        assert state["model"] == {"provider": "openai", "id": "original"}
        assert state["thinkingLevel"] == "low"
        assert retained_selection(adapter, "room-123") is None

    @pytest.mark.asyncio
    async def test_live_apply_sets_the_running_model(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        adapter = real_adapter(
            config=OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            emit=(),
        )
        await run_turn(adapter, FakeAgentTools())
        await adapter.apply_model_selection(
            ModelSelection(model="openai/replacement", reasoning_effort="low"),
            room_id="room-123",
        )
        client = owned_room(adapter, "room-123").client
        state = await client.request(OmpCommandName.GET_STATE)
        assert state["model"] == {"provider": "openai", "id": "replacement"}
        assert state["thinkingLevel"] == "low"
        assert (await sent_command_parameters(client, OmpCommandName.SET_MODEL))[
            -1
        ] == {
            "provider": "openai",
            "modelId": "replacement",
        }

    @pytest.mark.parametrize("invalid", ["unqualified", "/gpt-4"])
    @pytest.mark.asyncio
    async def test_invalid_model_is_rejected(
        self, tmp_path: Path, invalid: str, real_adapter: AdapterFactory
    ) -> None:
        adapter = real_adapter(
            config=OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            emit=(),
        )
        await run_turn(adapter, FakeAgentTools())
        with pytest.raises(ValueError, match="provider/model"):
            await adapter.apply_model_selection(
                ModelSelection(model=invalid),
                room_id="room-123",
            )
        client = owned_room(adapter, "room-123").client
        state = await client.request(OmpCommandName.GET_STATE)
        assert state["model"] == {"provider": "openai", "id": "original"}
        assert not any(
            parameters.get("provider") == ""
            for parameters in (
                await sent_command_parameters(client, OmpCommandName.SET_MODEL)
            )
        )

    @pytest.mark.asyncio
    async def test_thinking_mismatch_is_rejected(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        adapter = real_adapter(
            faults=(NativeFault.MISMATCH_EFFORT,),
            config=OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            emit=(),
        )
        await run_turn(adapter, FakeAgentTools())
        with pytest.raises(ValueError, match="did not apply"):
            await adapter.apply_model_selection(
                ModelSelection(reasoning_effort="low"),
                room_id="room-123",
            )
        state = await owned_room(adapter, "room-123").client.request(
            OmpCommandName.GET_STATE
        )
        assert state["thinkingLevel"] == "auto"

    @pytest.mark.asyncio
    async def test_failed_restore_retires_the_room(
        self, real_adapter: AdapterFactory
    ) -> None:
        adapter = real_adapter(
            config=OmpAdapterConfig(model="openai/original", reasoning_effort="low"),
            emit=(),
        )
        await run_turn(adapter, FakeAgentTools())
        retiring = owned_room(adapter, "room-123").client
        process = owned_rpc_process(retiring)
        await retiring.request(FAIL_MODEL_RESTORE)
        with pytest.raises(ValueError, match="reasoning effort"):
            await adapter.apply_model_selection(
                ModelSelection(model="openai/replacement", reasoning_effort="extreme"),
                room_id="room-123",
            )
        tools = FakeAgentTools()
        await run_turn(adapter, tools)
        assert tools.turn.replied
        assert process.returncode is not None
        assert await process.wait() == process.returncode
        state = await owned_room(adapter, "room-123").client.request(
            OmpCommandName.GET_STATE
        )
        assert state["model"] == {"provider": "openai", "id": "original"}
        assert state["thinkingLevel"] == "low"
