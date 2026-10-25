"""Batch-input probe for the crewai adapter; see ``batchprobes``.

crewai_flow declines batch input (each message is its own Flow run), so it has
no probe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from band.adapters.crewai import CrewAIAdapter
from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver

if TYPE_CHECKING:
    from tests.framework_conformance.batchprobes.crewaimodel import RecordingModel


async def recording_adapter(final_text: str) -> tuple[CrewAIAdapter, RecordingModel]:
    """A started adapter whose real CrewAI agent calls a model that records every
    request and ends each call with ``final_text``."""
    pytest.importorskip("crewai")
    from tests.framework_conformance.batchprobes.crewaimodel import (  # noqa: PLC0415 -- imports crewai, absent from some lane venvs
        RecordingModel,
    )

    adapter = CrewAIAdapter()
    await adapter.on_started("Agent", "A test agent")
    # CrewAI takes its model only through the agent it builds at startup.
    assert adapter._crewai_agent is not None
    model = RecordingModel(final_text)
    adapter._crewai_agent.llm = model
    return adapter, model


async def run_crewai(inp: AgentInput, final_text: str) -> str:
    adapter, model = await recording_adapter(final_text)
    await deliver(adapter, inp)
    return model.provider_input


PROBES: dict[str, BatchProbe] = {"crewai": BatchProbe(run=run_crewai)}
