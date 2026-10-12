"""Turn-outcome probes for the crewai adapter(s); see ``turnprobes``.

crewai_flow is exempt from judging (``judges_turns`` is false), so it has no
probe.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from band.adapters.crewai import EMPTY_LLM_RESPONSE_MARKER, CrewAIAdapter
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)


class ScriptedCrewAgent:
    """Stands in for the crew's LLM loop: runs the script's tool calls through
    the crew's real BaseTools, then ends the way CrewAI does -- the final text
    as ``raw``, or CrewAI's empty-completion error for a tool-only ending."""

    def __init__(self, script: TurnScript, crew_tools: list[Any]) -> None:
        self._script = script
        self._tools_by_name = {tool.name: tool for tool in crew_tools}

    async def kickoff_async(self, _prompt: str) -> SimpleNamespace:
        # CrewAI runs its synchronous tools off the event loop thread.
        await asyncio.to_thread(self._call_tools)
        if not self._script.final_text:
            raise ValueError(f"{EMPTY_LLM_RESPONSE_MARKER} - None or empty.")
        return SimpleNamespace(raw=self._script.final_text)

    def _call_tools(self) -> None:
        for call in self._script.tool_calls:
            self._tools_by_name[call.name].run(**call.arguments)


async def run_crewai(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("crewai")
    adapter = CrewAIAdapter()
    await adapter.on_started("Agent", "A test agent")
    assert adapter._crewai_agent is not None
    adapter._crewai_agent = ScriptedCrewAgent(script, adapter._crewai_agent.tools)
    await adapter.on_event(turn_input(tools))


PROBES: dict[str, TurnOutcomeProbe] = {"crewai": TurnOutcomeProbe(run=run_crewai)}
