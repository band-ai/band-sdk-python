"""Kit: CrewAI's real agent loop, driven by a scripted model, in each adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from crewai import Agent
from crewai.flow.flow import Flow, start

from band.adapters.crewai import CrewAIAdapter
from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
    WaitingDecision,
    get_current_flow_runtime,
)
from band.core.types import Capability
from band.testing.fake_tools import FakeAgentTools
from tests.crewaiscript import ScriptedLLM
from tests.framework_conformance.turnprobes import turn_input

#: Memory is enabled so a script can do real work before replying.
CAPABILITIES = frozenset({Capability.MEMORY})

TurnRunner = Callable[[ScriptedLLM, FakeAgentTools], Awaitable[None]]


async def run_crewai_turn(model: ScriptedLLM, tools: FakeAgentTools) -> None:
    """One turn through CrewAIAdapter, its crew agent thinking with ``model``."""
    adapter = CrewAIAdapter(capabilities=CAPABILITIES)
    try:
        await adapter.on_started("Agent", "A test agent")
        assert adapter._crewai_agent is not None
        # Swap only the model boundary; no credentialed LLM is reached.
        adapter._crewai_agent.llm = model
        await adapter.on_event(turn_input(tools))
    finally:
        await adapter.cleanup_all()


class SubCrewFlow(Flow):
    """A Flow whose step hands the turn to a sub-Crew agent, then decides."""

    def __init__(self, model: ScriptedLLM) -> None:
        super().__init__()
        self.model = model

    @start()
    async def delegate(self) -> dict[str, Any]:
        runtime = get_current_flow_runtime()
        if runtime is None:
            raise RuntimeError("Flow runtime is unavailable")
        agent = Agent(
            role="Room helper",
            goal="Handle the room's message",
            backstory="Answers through Band tools.",
            llm=self.model,
            tools=runtime.create_crewai_tools(),
        )
        await agent.kickoff_async("Handle the room's message")
        return WaitingDecision(
            decision="waiting", reason="Sub-crew finished"
        ).model_dump()


async def run_flow_turn(model: ScriptedLLM, tools: FakeAgentTools) -> None:
    """One turn through CrewAIFlowAdapter, its sub-Crew thinking with ``model``."""
    adapter = CrewAIFlowAdapter(
        CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
        flow_factory=lambda: SubCrewFlow(model),
        state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        capabilities=CAPABILITIES,
    )
    try:
        await adapter.on_started("Flow agent", "Hands turns to a sub-Crew")
        await adapter.on_event(turn_input(tools))
    finally:
        await adapter.cleanup_all()
