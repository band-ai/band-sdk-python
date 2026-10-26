"""Real RPC callback tasks may reenter room selection without deadlock."""

from __future__ import annotations

from band.adapters.omp import OmpAdapter
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import owned_room
from tests.adapters.omp.models import SelectingResolver
from tests.adapters.omp.turns import run_turn


class TestCallbackTaskReentry:
    async def test_permission_apply_on_callback_task_updates_the_live_model(
        self, selection_adapter: tuple[OmpAdapter, SelectingResolver]
    ) -> None:
        adapter, resolver = selection_adapter
        tools = FakeAgentTools()
        await run_turn(adapter, tools)
        assert resolver.called.is_set()
        assert tools.turn.replied
        state = await owned_room(adapter, "room-123").client.request(
            OmpCommandName.GET_STATE
        )
        assert state["model"] == {"provider": "openai", "id": "other"}
        assert state["thinkingLevel"] == "low"

    async def test_later_turn_reader_callback_updates_the_live_model(
        self, selection_adapter: tuple[OmpAdapter, SelectingResolver]
    ) -> None:
        adapter, resolver = selection_adapter
        await run_turn(adapter, FakeAgentTools())
        resolver.called.clear()
        resolver.selection = ModelSelection(
            model="openai/later", reasoning_effort="high"
        )

        tools = FakeAgentTools()
        await run_turn(adapter, tools, bootstrap=False)

        assert resolver.called.is_set()
        assert tools.turn.replied
        state = await owned_room(adapter, "room-123").client.request(
            OmpCommandName.GET_STATE
        )
        assert state["model"] == {"provider": "openai", "id": "later"}
        assert state["thinkingLevel"] == "high"
