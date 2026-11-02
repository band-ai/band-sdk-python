from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

pytest.importorskip("crewai", reason="crewai not installed (band-sdk[crewai])")

from crewai import Agent

from band.adapters.crewai import CrewAIAdapter
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.framework_conformance.turnprobes import ROOM_ID
from tests.integrations.crewai.completion.kit import ScriptedLLM, WorkInput


@pytest.fixture
def model() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def executions() -> list[bool]:
    return []


@pytest.fixture
async def adapter(
    model: ScriptedLLM, executions: list[bool]
) -> AsyncIterator[CrewAIAdapter]:
    @declares_turn_effect(TurnEffect.ACT)
    async def perform_work(args: WorkInput) -> str:
        executions.append(args.fail)
        if args.fail:
            raise RuntimeError("action failed")
        return "done"

    adapter = CrewAIAdapter(additional_tools=[(WorkInput, perform_work)])
    adapter._crewai_agent = Agent(
        role="Tester",
        goal="Validate turns",
        backstory="Use Band tools.",
        llm=model,
        tools=adapter.create_crewai_tools(),
    )
    try:
        yield adapter
    finally:
        await adapter.on_cleanup(ROOM_ID)
