"""Kit: a scripted model driving CrewAI's real agent loop through each adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from crewai import Agent, BaseLLM
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
from band.runtime.custom_tools import (
    CustomToolDef,
    declares_turn_effect,
    get_custom_tool_name,
)
from band.runtime.tools import TurnEffect
from band.testing.fake_tools import FakeAgentTools
from tests.crewaicalls import native_tool_call
from tests.framework_conformance.turnprobes import ToolCall, turn_input

#: Memory is enabled so a script can do real work before replying.
CAPABILITIES = frozenset({Capability.MEMORY})

#: The Band tool calls the model makes in one step.
Step = tuple[ToolCall, ...]


class StayQuiet(BaseModel):
    """Stay quiet this turn without posting to the room."""


@declares_turn_effect(TurnEffect.DECLINE)
async def stay_quiet(args: StayQuiet) -> str:
    return "Staying quiet"


#: A custom tool declared as a decline, registered beside the Band tools.
CUSTOM_TOOLS: list[CustomToolDef] = [(StayQuiet, stay_quiet)]
CUSTOM_DECLINE = ToolCall(get_custom_tool_name(StayQuiet))


class ScriptedModel(BaseLLM):
    """Answers each step it is asked for with the next scripted batch of tool
    calls, then ``final_text``; any further step is a failure, never a silent
    answer."""

    def __init__(self, *script: Step, final_text: str = "") -> None:
        super().__init__(model="scripted-turn")
        self.script = script
        self.final_text = final_text
        self.asked = 0

    def supports_function_calling(self) -> bool:
        return True

    def call(self, messages: Any, **kwargs: Any) -> Any:
        step = self.asked
        self.asked += 1
        if step < len(self.script):
            return [
                native_tool_call(f"call-{step}-{index}", str(call.name), call.arguments)
                for index, call in enumerate(self.script[step])
            ]
        if step == len(self.script):
            return self.final_text
        raise AssertionError(f"CrewAI asked for step {step + 1} of {self.script}")

    async def acall(self, messages: Any, **kwargs: Any) -> Any:
        return self.call(messages, **kwargs)


TurnRunner = Callable[[ScriptedModel, FakeAgentTools], Awaitable[None]]


async def run_crewai_turn(model: ScriptedModel, tools: FakeAgentTools) -> None:
    """One turn through CrewAIAdapter, its crew agent thinking with ``model``."""
    adapter = CrewAIAdapter(additional_tools=CUSTOM_TOOLS, capabilities=CAPABILITIES)
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

    def __init__(self, model: ScriptedModel) -> None:
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


async def run_flow_turn(model: ScriptedModel, tools: FakeAgentTools) -> None:
    """One turn through CrewAIFlowAdapter, its sub-Crew thinking with ``model``."""
    adapter = CrewAIFlowAdapter(
        CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
        flow_factory=lambda: SubCrewFlow(model),
        state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        additional_tools=CUSTOM_TOOLS,
        capabilities=CAPABILITIES,
    )
    try:
        await adapter.on_started("Flow agent", "Hands turns to a sub-Crew")
        await adapter.on_event(turn_input(tools))
    finally:
        await adapter.cleanup_all()
