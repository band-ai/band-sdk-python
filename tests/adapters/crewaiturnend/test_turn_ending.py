"""A decline ends CrewAI's agent loop; any other tool lets it continue."""

from __future__ import annotations

from typing import Any
from unittest.mock import ANY

import pytest
from crewai.utilities.agent_utils import MAX_REPEATED_TOOL_CALLS

from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import TurnRunner, run_crewai_turn, run_flow_turn
from tests.crewaiscript import ScriptedLLM, step
from tests.framework_conformance.outcomeprobes import ANSWER, DECLINE, REPLY
from tests.framework_conformance.turnprobes import ToolCall, turn_tools
from tests.mcpclient import STORE_MEMORY_ARGS

STORE_MEMORY = ToolCall(BandTool.STORE_MEMORY, STORE_MEMORY_ARGS)
#: A decline whose arguments CrewAI rejects before the tool runs.
INVALID_DECLINE = ToolCall(BandTool.NO_REPLY, {"reason": ["not", "text"]})

#: The one memory a scripted STORE_MEMORY leaves, with the fields the fake fills in.
STORED_MEMORY: dict[str, Any] = {
    **STORE_MEMORY_ARGS,
    "id": ANY,
    "inserted_at": ANY,
    "metadata": None,
    "organization_id": None,
    "source_agent_id": None,
    "status": "active",
    "subject_id": None,
}

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
        model = ScriptedLLM(*[step(DECLINE)] * MAX_REPEATED_TOOL_CALLS)
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 1
        assert tools.chat == []
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", RUNNERS)
    async def test_a_rejected_decline_lets_the_model_try_again(
        self, run_turn: TurnRunner
    ) -> None:
        model = ScriptedLLM(step(INVALID_DECLINE), step(DECLINE))
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 2
        assert tools.chat == []
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", RUNNERS)
    async def test_work_before_a_reply_lets_the_agent_continue(
        self, run_turn: TurnRunner
    ) -> None:
        model = ScriptedLLM(step(STORE_MEMORY), step(REPLY), "Done.")
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 3
        assert tools.memories == [STORED_MEMORY]
        assert tools.chat == [ANSWER]
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", RUNNERS)
    @pytest.mark.parametrize(
        ("calls", "memories"),
        [
            pytest.param((STORE_MEMORY, DECLINE), [STORED_MEMORY], id="work-first"),
            pytest.param((DECLINE, STORE_MEMORY), [], id="decline-first"),
        ],
    )
    async def test_a_decline_also_ends_its_own_step(
        self,
        run_turn: TurnRunner,
        calls: tuple[ToolCall, ...],
        memories: list[dict[str, Any]],
    ) -> None:
        """Work the model puts ahead of the decline in the same step runs;
        a call after it does not, since the decline ends the whole turn."""
        model = ScriptedLLM(step(*calls))
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 1
        assert tools.memories == memories
        assert tools.chat == []
        assert failure_reports(tools) == []
