"""Who a decline belongs to: the model's own resource work, or a subagent."""

from __future__ import annotations

import asyncio

from band.core.turn_lifecycle import TurnTask, resource_tools
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.runtime.tools.types import TurnEffect
from band.runtime.turn import declined_turn_refusal, record_effect
from band.testing import FakeAgentTools


def current_owner() -> TurnTask:
    task = asyncio.current_task()
    assert task is not None
    return TurnTask(task)


class TestDeclineOrigin:
    async def test_a_decline_through_a_resource_view_is_the_models(self) -> None:
        tools = FakeAgentTools()
        view = resource_tools(tools, current_owner())

        record_effect(view, TurnEffect.DECLINE)

        assert tools.turn.replied
        assert declined_turn_refusal(tools) == DECLINED_TURN_REFUSAL

    async def test_a_subagents_decline_ends_only_its_own_calls(self) -> None:
        tools = FakeAgentTools()
        subagent = resource_tools(tools, current_owner(), subagent=True)

        record_effect(subagent, TurnEffect.DECLINE)

        assert not tools.turn.replied
        assert tools.turn.complete
        assert declined_turn_refusal(tools) is None
        assert declined_turn_refusal(subagent) == DECLINED_TURN_REFUSAL
