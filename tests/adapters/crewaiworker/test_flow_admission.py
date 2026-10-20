"""Saved Flow entry points cannot reuse resources after their turn closes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
)
from band.core.exceptions import InputNotAccepted
from tests.adapters.crewaiworker.flow import (
    SavedFlow,
    call_saved_runtime,
    call_saved_subcrew_tool,
)
from tests.adapters.crewaiworker.kit import ReadResource
from tests.adapters.crewaiworker.peer import resource_peer
from tests.framework_conformance.turnprobes import turn_input, turn_tools


class TestFlowAdmission:
    @pytest.mark.parametrize(
        "invoke_late",
        [call_saved_runtime, call_saved_subcrew_tool],
        ids=["direct-runtime", "subcrew-tool"],
    )
    async def test_completed_turn_rejects_both_saved_tool_entry_points(
        self, invoke_late: Callable[[SavedFlow], Awaitable[Any]]
    ) -> None:
        async with resource_peer() as (client, expected):
            flow = SavedFlow(client)
            adapter = CrewAIFlowAdapter(
                CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
                flow_factory=lambda: flow,
                state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
                additional_tools=[(ReadResource, flow.read)],
            )
            try:
                await adapter.on_started("Flow reader", "Reads a resource")
                await adapter.on_event(turn_input(turn_tools()))
                assert flow.result == expected
                with pytest.raises(InputNotAccepted, match="turn has ended"):
                    await invoke_late(flow)
                assert flow.calls == 1
                assert not client.is_closed
            finally:
                await adapter.cleanup_all()
