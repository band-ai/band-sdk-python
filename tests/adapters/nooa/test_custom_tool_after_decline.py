"""A custom tool called after the turn declined is refused, not run."""

from __future__ import annotations

import pytest

from band.adapters.nooa.tools import custom_tool_spec, room_tools_class
from band.core.exceptions import BandToolError
from band.runtime.tools.types import TurnEffect
from band.runtime.turn import DECLINED_TURN_REFUSAL, record_effect
from band.testing import FakeAgentTools
from tests.adapters.nooa.kit import OrderInput


class TestCustomToolAfterDecline:
    async def test_the_handler_never_runs_after_a_decline(self) -> None:
        ran: list[str] = []

        async def order(args: OrderInput) -> str:
            ran.append(args.order_id)
            return "shipped"

        tools = FakeAgentTools()
        record_effect(tools, TurnEffect.DECLINE)
        room = room_tools_class([custom_tool_spec((OrderInput, order))])(
            emit_tool_calls=False
        )
        async with room.bound_to(tools):
            with pytest.raises(BandToolError) as refused:
                await room.order(arguments={"order_id": "A-1"})

        assert str(refused.value.__cause__) == DECLINED_TURN_REFUSAL
        assert ran == []
