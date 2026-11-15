"""One room message through the agent's ``respond`` method, and how it ends."""

from __future__ import annotations

from unittest.mock import ANY

import pytest
from nooa import GenerationError

from band.core.types import Emit, ToolEventKey
from band.runtime.tools import BandTool
from band.runtime.tools.schema import tool_execution_failure
from band.testing import FakeAgentTools, failure_reports
from band.testing.nooa import code_turn, custom_room_call, room_call, scripted_llm
from tests.adapters.nooa.kit import (
    OrderInput,
    dispatched,
    failing_llm,
    prompt_text,
    rejecting_send_tools,
    run_turn,
    started_adapter,
    tool_results,
)

REPLY = {"content": "Hi Alice", "mentions": ["Alice"]}
ORDER_SERVICE_DOWN = "the order service is down"


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

    async def test_a_raising_custom_tool_raises_into_the_model_session(
        self, tools: FakeAgentTools
    ) -> None:
        failure = ValueError(ORDER_SERVICE_DOWN)

        def broken_order(args: OrderInput) -> str:
            raise failure

        llm = scripted_llm(
            "try:\n"
            f"    {custom_room_call('order', {'order_id': 'A-1'})}\n"
            "except Exception as error:\n"
            "    print(type(error).__name__, error)\n"
            f"{room_call(BandTool.NO_REPLY)}"
        )
        adapter = await started_adapter(
            llm,
            additional_tools=[(OrderInput, broken_order)],
            emit={Emit.TOOL_CALLS},
        )

        await run_turn(adapter, tools)

        reported = f"order failed: {tool_execution_failure('order', failure)}"
        assert f"BandToolError {reported}" in prompt_text(llm)
        assert tool_results(tools)[0] == {
            ToolEventKey.NAME: "order",
            ToolEventKey.OUTPUT: reported,
            ToolEventKey.TOOL_CALL_ID: ANY,
            ToolEventKey.IS_ERROR: True,
        }
        assert dispatched(tools) == [BandTool.NO_REPLY]
        assert failure_reports(tools) == []

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
        assert (
            "[System]: Bob joined the room\n\n## Current input messages (1)" in prompt
        )
