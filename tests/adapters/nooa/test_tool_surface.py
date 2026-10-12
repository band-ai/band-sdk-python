"""The ``self.room`` tools the model is told about and can call."""

from __future__ import annotations

from band.core.types import Capability
from band.testing import FakeAgentTools
from band.testing.nooa import room_call, scripted_llm
from tests.adapters.nooa.kit import (
    OrderInput,
    advertised_tools,
    find_order,
    prompt_text,
    run_turn,
    started_adapter,
)


class TestRoomToolSurface:
    async def test_the_model_is_told_only_the_tools_its_features_allow(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(room_call("band_no_reply"))
        adapter = await started_adapter(
            llm, include_tools=["band_send_message", "band_no_reply"]
        )

        await run_turn(adapter, tools)

        assert advertised_tools(llm) == ["band_send_message", "band_no_reply"]

    async def test_a_capability_adds_its_tools(self, tools: FakeAgentTools) -> None:
        llm = scripted_llm(room_call("band_no_reply"))
        adapter = await started_adapter(llm, capabilities={Capability.MEMORY})

        await run_turn(adapter, tools)

        assert "band_store_memory" in advertised_tools(llm)

    async def test_the_model_reads_each_tools_argument_descriptions(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(room_call("band_no_reply"))
        adapter = await started_adapter(llm)

        await run_turn(adapter, tools)

        assert "mentions: List of participants to mention." in prompt_text(llm)

    async def test_the_turn_binding_is_hidden_from_the_model(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(room_call("band_no_reply"))
        adapter = await started_adapter(llm)

        await run_turn(adapter, tools)

        assert "bound_to" not in prompt_text(llm)

    async def test_a_custom_tool_is_a_room_method_whose_effect_completes_the_turn(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(
            f"order = {room_call('order', {'order_id': 'A-1'})}\nprint(order)"
        )
        adapter = await started_adapter(
            llm, additional_tools=[(OrderInput, find_order)]
        )

        await run_turn(adapter, tools)

        assert "order" in advertised_tools(llm)
        assert "'status': 'shipped'" in prompt_text(llm)
        assert tools.turn.complete
