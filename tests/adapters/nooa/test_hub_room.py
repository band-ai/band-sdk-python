"""The contacts hub room always offers the contact tools."""

from __future__ import annotations

from band.runtime.tools import CONTACT_TOOL_NAMES, BandTool
from band.testing import FakeAgentTools
from band.testing.nooa import room_call, scripted_llm
from tests.adapters.nooa.kit import (
    ROOM_ID,
    advertised_tools,
    run_turn,
    started_adapter,
)


class TestHubRoom:
    async def test_the_hub_room_offers_contact_tools_the_features_leave_out(
        self,
    ) -> None:
        llm = scripted_llm(room_call(BandTool.NO_REPLY))
        adapter = await started_adapter(llm)

        await run_turn(adapter, FakeAgentTools(room_id=ROOM_ID, hub_room_id=ROOM_ID))

        assert CONTACT_TOOL_NAMES <= set(advertised_tools(llm))

    async def test_an_ordinary_room_keeps_to_the_features(self) -> None:
        llm = scripted_llm(room_call(BandTool.NO_REPLY))
        adapter = await started_adapter(llm)

        await run_turn(adapter, FakeAgentTools(room_id=ROOM_ID))

        assert not CONTACT_TOOL_NAMES & set(advertised_tools(llm))
