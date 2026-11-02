"""A decline ends CrewAI's agent loop; any other Band tool lets it continue."""

from __future__ import annotations

import pytest
from crewai.utilities.agent_utils import MAX_REPEATED_TOOL_CALLS

from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import (
    ScriptedModel,
    TurnRunner,
    run_crewai_turn,
    run_flow_turn,
)
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY
from tests.framework_conformance.turnprobes import ToolCall, TurnScript, turn_tools
from tests.mcpclient import STORE_MEMORY_ARGS

DECLINE = ToolCall(BandTool.NO_REPLY, {"reason": "No current input"})
STORE_MEMORY = ToolCall(BandTool.STORE_MEMORY, STORE_MEMORY_ARGS)

RUNNERS = [
    pytest.param(run_crewai_turn, id="crewai"),
    pytest.param(run_flow_turn, id="crewai_flow"),
]


class TestBandToolTurnEnding:
    @pytest.mark.parametrize("run_turn", RUNNERS)
    async def test_a_decline_is_the_agents_last_step(
        self, run_turn: TurnRunner
    ) -> None:
        """A model that would keep declining until CrewAI's repeat guard
        fails the run is asked for one step, and the turn completes."""
        model = ScriptedModel(TurnScript((DECLINE,) * MAX_REPEATED_TOOL_CALLS))
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.steps == 1
        assert tools.chat == []
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", RUNNERS)
    async def test_work_before_a_reply_lets_the_agent_continue(
        self, run_turn: TurnRunner
    ) -> None:
        script = TurnScript((STORE_MEMORY, REPLY), final_text="Done.")
        model = ScriptedModel(script)
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.steps == len(script.tool_calls) + 1
        assert STORE_MEMORY_ARGS.items() <= tools.memories[-1].items()
        assert tools.chat == [ANSWER]
        assert failure_reports(tools) == []
