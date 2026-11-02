from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

pytest.importorskip("crewai", reason="crewai not installed (band-sdk[crewai])")

from band.adapters.crewai import CrewAIAdapter
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.crewaiscript import ScriptedLLM
from tests.framework_conformance.turnprobes import ROOM_ID
from tests.integrations.crewai.completion.kit import WorkInput


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
    try:
        await adapter.on_started("Tester", "Validates turns")
        assert adapter._crewai_agent is not None
        # Swap only the model boundary; no credentialed LLM is reached.
        adapter._crewai_agent.llm = model
        yield adapter
    finally:
        await adapter.on_cleanup(ROOM_ID)
