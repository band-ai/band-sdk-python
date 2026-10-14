"""One room message through the agent's ``respond`` method, and how it ends."""

from __future__ import annotations

import pytest
from nooa import GenerationError

from band.runtime.tools import BandTool
from band.testing import FakeAgentTools, failure_reports
from band.testing.nooa import code_turn, room_call, scripted_llm
from tests.adapters.nooa.kit import (
    dispatched,
    failing_llm,
    prompt_text,
    rejecting_send_tools,
    run_turn,
    started_adapter,
)

REPLY = {"content": "Hi Alice", "mentions": ["Alice"]}


class TestRoomTurn:
    async def test_a_room_tool_call_in_generated_code_is_dispatched(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            scripted_llm(room_call(BandTool.SEND_MESSAGE, REPLY))
        )

        await run_turn(adapter, tools)

        assert tools.tool_calls == [
            {"tool_name": BandTool.SEND_MESSAGE, "arguments": REPLY}
        ]

    async def test_a_failed_tool_call_raises_into_the_model_session(self) -> None:
        tools = rejecting_send_tools("the platform rejected the message")
        llm = scripted_llm(
            "try:\n"
            f"    {room_call(BandTool.SEND_MESSAGE, REPLY)}\n"
            "except Exception as error:\n"
            "    print(type(error).__name__, error)\n"
            f"{room_call(BandTool.NO_REPLY)}"
        )
        adapter = await started_adapter(llm)

        await run_turn(adapter, tools)

        assert (
            "BandToolError band_send_message failed: the platform rejected the message"
            in prompt_text(llm)
        )
        assert dispatched(tools) == [BandTool.SEND_MESSAGE, BandTool.NO_REPLY]

    async def test_a_model_failure_is_reported_once_and_raised(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(failing_llm("provider down"))

        with pytest.raises(GenerationError, match="provider down"):
            await run_turn(adapter, tools)

        assert [provider for provider, _ in failure_reports(tools)] == ["nooa"]
        assert dispatched(tools) == []

    async def test_a_failure_after_the_reply_leaves_the_turn_settled(
        self, tools: FakeAgentTools
    ) -> None:
        llm = failing_llm(
            "provider down", code_turn(room_call(BandTool.SEND_MESSAGE, REPLY))
        )
        adapter = await started_adapter(llm)

        await run_turn(adapter, tools)

        assert failure_reports(tools) == []
        assert dispatched(tools) == [BandTool.SEND_MESSAGE]

    async def test_a_platform_update_leads_the_message(
        self, tools: FakeAgentTools
    ) -> None:
        llm = scripted_llm(room_call(BandTool.NO_REPLY))
        adapter = await started_adapter(llm)

        await run_turn(adapter, tools, participants_msg="Bob joined the room")

        prompt = prompt_text(llm)
        assert "[System]: Bob joined the room\n\n[Alice]: @Agent hello" in prompt
