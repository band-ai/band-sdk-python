"""The ``self.room`` tools the model is told about and can call."""

from __future__ import annotations

from band.core.types import Capability
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.nooa import room_call, scripted_llm
from tests.adapters.nooa.kit import (
    LookupOrderInput,
    OrderInput,
    TaggedInput,
    advertised_tools,
    find_order,
    lookup_order,
    prompt_text,
    run_turn,
    started_adapter,
    tag_order,
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

    async def test_a_factory_default_is_documented_without_calling_it(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(room_call("band_no_reply"))
        adapter = await started_adapter(
            llm, additional_tools=[(TaggedInput, tag_order)]
        )

        await run_turn(adapter, tools)

        assert "tags: list[str] = ..." in prompt_text(llm)

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

    async def test_an_aliased_custom_tool_is_called_by_its_field_names(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(
            f"print({room_call('lookuporder', {'order_id': 'A-1'})})\n"
            f"{room_call(BandTool.NO_REPLY)}"
        )
        adapter = await started_adapter(
            llm, additional_tools=[(LookupOrderInput, lookup_order)]
        )

        await run_turn(adapter, tools)

        assert "A-1 is shipped" in prompt_text(llm)
