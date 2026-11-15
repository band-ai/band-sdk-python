"""A native decline prevents subsequent model tool actions."""

from __future__ import annotations

from band_sdk_core import TurnVerdict

from band.runtime.tools import BandTool
from tests.framework_conformance.outcomeprobes import (
    REPLY,
    TURN_OUTCOME_PROBES,
    TurnResult,
    run_turn,
)
from tests.framework_conformance.turnprobes import ToolCall, TurnScript


class TestOmpTurnTermination:
    async def test_decline_stops_the_remaining_tool_actions(self) -> None:
        script = TurnScript(
            (ToolCall(BandTool.NO_REPLY, {"reason": "FYI only"}), REPLY)
        )
        result = await run_turn(TURN_OUTCOME_PROBES["omp"], script)
        assert result == TurnResult(TurnVerdict.Complete, messages=[], failures=[])
