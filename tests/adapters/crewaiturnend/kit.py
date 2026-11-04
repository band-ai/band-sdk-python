"""Kit: CrewAI's real agent loop, driven by a scripted model, in each adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from crewai import Agent
from crewai.flow.flow import Flow, start
from pydantic import BaseModel

from band.adapters.crewai import CrewAIAdapter
from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
    WaitingDecision,
    get_current_flow_runtime,
)
from band.core.types import Capability
from band.runtime.custom_tools import CustomToolDef
from band.testing.fake_tools import FakeAgentTools
from tests.crewaiscript import ScriptedLLM, react_step, step
from tests.framework_conformance.turnprobes import (
    DispatchingFakeTools,
    ToolCall,
    turn_input,
)

#: Memory is enabled so a script can do real work before replying.
CAPABILITIES = frozenset({Capability.MEMORY})

TurnRunner = Callable[[ScriptedLLM, FakeAgentTools], Awaitable[None]]


async def run_crewai_turn(
    model: ScriptedLLM,
    tools: FakeAgentTools,
    *,
    custom_tools: list[CustomToolDef] | None = None,
    failure_policy: Any = None,
) -> None:
    """One turn through CrewAIAdapter, its crew agent thinking with ``model``."""
    adapter = CrewAIAdapter(capabilities=CAPABILITIES, additional_tools=custom_tools)
    try:
        await adapter.on_started("Agent", "A test agent")
        assert adapter._crewai_agent is not None
        # Swap only the model boundary; no credentialed LLM is reached.
        adapter._crewai_agent.llm = model
        adapter._crewai_agent.tool_failure_policy = failure_policy
        await adapter.on_event(turn_input(tools))
    finally:
        await adapter.cleanup_all()


class SubCrewFlow(Flow):
    """A Flow whose step hands the turn to a sub-Crew agent, then decides."""

    def __init__(
        self, model: ScriptedLLM, after: Any = None, failure_policy: Any = None
    ) -> None:
        super().__init__()
        self.model = model
        self.after = after
        self.failure_policy = failure_policy

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
            tool_failure_policy=self.failure_policy,
        )
        await agent.kickoff_async("Handle the room's message")
        if self.after is not None:
            return await self.after(runtime)
        return WaitingDecision(
            decision="waiting", reason="Sub-crew finished"
        ).model_dump()


async def run_flow_turn(
    model: ScriptedLLM,
    tools: FakeAgentTools,
    *,
    custom_tools: list[CustomToolDef] | None = None,
    after: Any = None,
    failure_policy: Any = None,
) -> None:
    """One turn through CrewAIFlowAdapter, its sub-Crew thinking with ``model``."""
    adapter = CrewAIFlowAdapter(
        CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
        flow_factory=lambda: SubCrewFlow(model, after, failure_policy),
        state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        capabilities=CAPABILITIES,
        additional_tools=custom_tools,
    )
    try:
        await adapter.on_started("Flow agent", "Hands turns to a sub-Crew")
        await adapter.on_event(turn_input(tools))
    finally:
        await adapter.cleanup_all()


class DeclineInput(BaseModel):
    """Settle this request without posting a reply."""

    reason: str


class WorkInput(BaseModel):
    """Perform the next step."""

    value: str


def scripted_model(function_calling: bool, *calls: ToolCall) -> ScriptedLLM:
    """Build explicit native or ReAct responses, followed by a final answer."""
    response = step if function_calling else react_step
    return ScriptedLLM(
        *(response(call) for call in calls),
        "Thought: I have finished.\nFinal Answer: Done.",
        function_calling=function_calling,
    )


def legacy_terminal(handler: Any) -> Any:
    """Apply the existing ACT shorthand to a test handler."""
    handler.band_terminal = True
    return handler


class UnavailableDeclineTools(DispatchingFakeTools):
    """Fail the platform call at its transport-facing tools boundary."""

    async def no_reply(self, reason: str | None = None) -> dict[str, str]:
        raise ValueError("decline unavailable")
