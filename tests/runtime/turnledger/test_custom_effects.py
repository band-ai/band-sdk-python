"""Custom tools record declared effects only after successful execution."""

from __future__ import annotations

from typing import Any

import pytest

from band.core.delivery import relay_reply
from band.core.turn import report_unsettled_turn
from band.runtime.custom_tools import declares_turn_effect, execute_custom_tool
from band.runtime.tools import TurnEffect
from band.testing.fake_tools import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
)
from tests.runtime.turnledger.kit import LookupInput


class TestCustomTools:
    @pytest.mark.parametrize("output", ["filed", None])
    async def test_a_declared_tool_records_its_effect(self, output: str | None) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def post_to_ticketing(args: LookupInput) -> str | None:
            return output

        tools = FakeAgentTools()
        await execute_custom_tool(
            (LookupInput, post_to_ticketing), {"topic": "x"}, tools=tools
        )

        assert tools.turn.complete

    async def test_an_undeclared_tool_only_observes(self) -> None:
        async def lookup(args: LookupInput) -> str:
            return "found"

        tools = FakeAgentTools()
        await execute_custom_tool((LookupInput, lookup), {"topic": "x"}, tools=tools)

        assert not tools.turn.complete

    async def test_a_failing_declared_tool_records_nothing(self) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def broken(args: LookupInput) -> str:
            raise RuntimeError("down")

        tools = FakeAgentTools()
        with pytest.raises(RuntimeError):
            await execute_custom_tool(
                (LookupInput, broken), {"topic": "x"}, tools=tools
            )

        assert not tools.turn.complete

    @pytest.mark.parametrize(
        "effect", [TurnEffect.ACT, TurnEffect.REPLY, TurnEffect.DECLINE]
    )
    @pytest.mark.parametrize(
        "output",
        [
            {"ok": False, "error": "upstream refused"},
            "Error: upstream refused",
            "Error executing ticket: upstream refused",
        ],
    )
    async def test_a_failure_value_does_not_complete_the_turn(
        self, effect: TurnEffect, output: Any
    ) -> None:
        @declares_turn_effect(effect)
        async def refused(args: LookupInput) -> Any:
            return output

        tools = FakeAgentTools()
        await execute_custom_tool((LookupInput, refused), {"topic": "x"}, tools=tools)

        assert await report_unsettled_turn(tools, room_id="room-1")
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    @pytest.mark.parametrize("effect", [TurnEffect.REPLY, TurnEffect.DECLINE])
    async def test_a_failure_value_does_not_suppress_the_fallback(
        self, effect: TurnEffect
    ) -> None:
        @declares_turn_effect(effect)
        async def refused(args: LookupInput) -> dict[str, Any]:
            return {"ok": False, "error": "upstream refused"}

        tools = FakeAgentTools()
        await execute_custom_tool((LookupInput, refused), {"topic": "x"}, tools=tools)
        await relay_reply(tools, "The operation failed.", ["@alice"])

        assert tools.chat == ["The operation failed."]
