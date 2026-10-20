from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
)
from band.testing.platform import platform_connection_stub
from tests.adapters.crewaiflow.kit import empty_flow


class TestOnStartedAndNamespace:
    @pytest.mark.asyncio
    async def test_default_namespace_resolves_with_agent_id(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=empty_flow)
        adapter.platform = platform_connection_stub(agent_id="agent-id-A")
        await adapter.on_started("Router", "desc")
        assert adapter.metadata_namespace == "crewai_flow:agent-id-A"

    @pytest.mark.asyncio
    async def test_two_adapters_get_distinct_namespaces(self) -> None:
        a = CrewAIFlowAdapter(flow_factory=empty_flow)
        b = CrewAIFlowAdapter(flow_factory=empty_flow)
        a.platform = platform_connection_stub(agent_id="agent-id-X")
        b.platform = platform_connection_stub(agent_id="agent-id-Y")
        await a.on_started("Router", "")
        await b.on_started("Router", "")
        assert a.metadata_namespace != b.metadata_namespace

    @pytest.mark.asyncio
    async def test_explicit_namespace_overrides_default(self) -> None:
        adapter = CrewAIFlowAdapter(
            CrewAIFlowAdapterConfig(metadata_namespace="custom_ns"),
            flow_factory=empty_flow,
        )
        await adapter.on_started("agent-A", "")
        assert adapter.metadata_namespace == "custom_ns"

    @pytest.mark.asyncio
    async def test_on_started_does_not_call_flow_factory(self) -> None:
        called = []

        def factory():
            called.append(1)
            return MagicMock()

        adapter = CrewAIFlowAdapter(flow_factory=factory)
        await adapter.on_started("agent-A", "")
        assert called == []
