"""What NOOA lists for the model as already in scope for its code."""

from __future__ import annotations

from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.nooa import room_call, scripted_llm
from tests.adapters.nooa.kit import execution_context, run_turn, started_adapter


class TestModelContext:
    async def test_no_adapter_internals_are_in_scope_for_the_model(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(room_call(BandTool.NO_REPLY))
        adapter = await started_adapter(llm)

        await run_turn(adapter, tools)

        in_scope = execution_context(llm)
        leaked = [
            name
            for name in ("NooaAdapter", "TurnObserver", "with_room_tools", "set_hooks")
            if name in in_scope
        ]
        assert leaked == []
