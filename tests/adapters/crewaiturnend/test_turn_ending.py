"""A decline ends CrewAI's agent loop; any other tool lets it continue."""

from __future__ import annotations

from typing import Any
from unittest.mock import ANY

import pytest
from crewai.utilities.agent_utils import MAX_REPEATED_TOOL_CALLS

from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import (
    CUSTOM_DECLINE,
    ScriptedModel,
    TurnRunner,
    run_crewai_turn,
    run_flow_turn,
)
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY
from tests.framework_conformance.turnprobes import ToolCall, turn_tools
from tests.mcpclient import STORE_MEMORY_ARGS

DECLINE = ToolCall(BandTool.NO_REPLY, {"reason": "No current input"})
STORE_MEMORY = ToolCall(BandTool.STORE_MEMORY, STORE_MEMORY_ARGS)

#: The one memory a scripted STORE_MEMORY leaves; the fake assigns its id and time.
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
    @pytest.mark.parametrize(
        "decline",
        [
            pytest.param(DECLINE, id="band_no_reply"),
            pytest.param(CUSTOM_DECLINE, id="declared-custom"),
        ],
    )
    async def test_a_decline_is_the_agents_last_step(
        self, run_turn: TurnRunner, decline: ToolCall
    ) -> None:
        """A model that would keep declining until CrewAI's repeat guard
        fails the run is asked for one step, and the turn completes."""
        model = ScriptedModel(*[(decline,)] * MAX_REPEATED_TOOL_CALLS)
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 1
        assert tools.chat == []
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", RUNNERS)
    async def test_work_before_a_reply_lets_the_agent_continue(
        self, run_turn: TurnRunner
    ) -> None:
        model = ScriptedModel((STORE_MEMORY,), (REPLY,), final_text="Done.")
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 3
        assert tools.memories == [STORED_MEMORY]
        assert tools.chat == [ANSWER]
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", RUNNERS)
    async def test_work_in_the_declining_step_runs_before_the_turn_ends(
        self, run_turn: TurnRunner
    ) -> None:
        """The decline ends the turn, but work the model put ahead of it in
        the same step still happens."""
        model = ScriptedModel((STORE_MEMORY, DECLINE))
        tools = turn_tools()

        await run_turn(model, tools)

        assert model.asked == 1
        assert tools.memories == [STORED_MEMORY]
        assert tools.chat == []
        assert failure_reports(tools) == []
