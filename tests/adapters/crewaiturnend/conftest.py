from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("crewai")


from contextlib import asynccontextmanager
from uuid import uuid4

from band.adapters.crewai import CrewAIAdapter
from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
)
from tests.adapters.crewaiturnend.kit import SubCrewFlow


@pytest.fixture
def retained_adapter() -> Any:
    @asynccontextmanager
    async def start_adapter(kind: str, model: Any, custom_tools: Any) -> Any:
        if kind == "crewai":
            adapter = CrewAIAdapter(additional_tools=custom_tools)
        elif kind == "flow":
            adapter = CrewAIFlowAdapter(
                CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
                flow_factory=lambda: SubCrewFlow(model),
                state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
                additional_tools=custom_tools,
            )
        else:
            raise ValueError(f"Unsupported adapter: {kind}")
        try:
            await adapter.on_started("Agent", "Handles test turns")
            if kind == "crewai":
                adapter._crewai_agent.llm = model
            yield adapter
        finally:
            await adapter.cleanup_all()

    return start_adapter
