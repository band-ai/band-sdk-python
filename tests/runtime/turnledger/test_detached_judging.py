"""Detached turns report failures only after a turn was judged."""

from __future__ import annotations

from band.core.turn import judge_detached_turn
from band.testing.fake_tools import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)


class TestJudgeDetachedTurn:
    async def test_a_judged_detached_turn_is_reported(self) -> None:
        tools = FakeAgentTools()
        tools.turn.judged = True
        tools.turn.detach()

        await judge_detached_turn(tools, room_id="room-1")

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    async def test_an_unjudged_detached_turn_is_never_reported(self) -> None:
        """A contact-hub turn parked on a decision is never reported later."""
        tools = FakeAgentTools()
        tools.turn.detach()

        await judge_detached_turn(tools, room_id="room-1")

        assert reported_failures(tools) == []
