"""A decline ends a provider-run turn: the model's later tool calls do nothing."""

from __future__ import annotations

import pytest
from band_sdk_core import TurnVerdict

from band.runtime.tools import BandTool
from tests.framework_conformance.outcomeprobes import (
    DECLINE,
    REPLY,
    TURN_OUTCOME_PROBES,
    TurnResult,
    provider_loop_probe_params,
    run_turn,
)
from tests.framework_conformance.turnprobes import ALICE, ToolCall, TurnScript

ADD_ALICE = ToolCall(BandTool.ADD_PARTICIPANT, {"identifier": ALICE["handle"]})


class TestTurnTermination:
    @pytest.mark.parametrize("later", [REPLY, ADD_ALICE], ids=["reply", "act"])
    @pytest.mark.parametrize("probe", provider_loop_probe_params())
    async def test_a_decline_refuses_every_later_tool_call(
        self, probe: str, later: ToolCall
    ) -> None:
        result = await run_turn(
            TURN_OUTCOME_PROBES[probe], TurnScript((DECLINE, later))
        )

        assert result == TurnResult(TurnVerdict.Complete, messages=[], failures=[])
