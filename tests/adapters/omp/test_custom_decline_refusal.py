"""A custom decline ends a native OMP turn: the model's later Band calls do nothing."""

from __future__ import annotations

from band_sdk_core import TurnVerdict
from pydantic import BaseModel

from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.outcomeprobes import REPLY, TurnResult, run_turn
from tests.framework_conformance.turnprobes import (
    ToolCall,
    TurnLoop,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)
from tests.framework_conformance.turnprobes.omp import scripted_omp_adapter


class SkipInput(BaseModel):
    """Decline to reply to this message."""


@declares_turn_effect(TurnEffect.DECLINE)
async def skip(args: SkipInput) -> str:
    return "skipped"


async def run_omp_with_skip(script: TurnScript, tools: FakeAgentTools) -> None:
    async with scripted_omp_adapter(
        script, additional_tools=[(SkipInput, skip)]
    ) as adapter:
        await adapter.on_event(turn_input(tools))


class TestCustomDeclineRefusal:
    async def test_a_reply_after_a_custom_decline_posts_nothing(self) -> None:
        probe = TurnOutcomeProbe(loop=TurnLoop.PROVIDER, run=run_omp_with_skip)

        result = await run_turn(
            probe, TurnScript((ToolCall(get_custom_tool_name(SkipInput)), REPLY))
        )

        assert result == TurnResult(
            TurnVerdict.Complete, messages=[], failures=[], participants_added=[]
        )
