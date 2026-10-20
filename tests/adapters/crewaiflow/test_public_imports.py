from __future__ import annotations

import pytest

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
)
from band.testing.platform import platform_connection_stub
from tests.adapters.crewaiflow.kit import empty_flow


class TestPublicImportPath:
    def test_lazy_import_from_band_adapters(self) -> None:
        # The example imports `from band.adapters import CrewAIFlowAdapter`; this
        # import must stay local, since it's the band.adapters lazy loader itself
        # under test, not just a way to reach the class.
        from band.adapters import CrewAIFlowAdapter as Imported  # noqa: PLC0415

        assert Imported is CrewAIFlowAdapter

    @pytest.mark.asyncio
    async def test_default_namespace_matches_documented_format(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=empty_flow)
        adapter.platform = platform_connection_stub(agent_id="crewai-flow-router-id")
        await adapter.on_started("crewai_flow_router", "")
        assert adapter.metadata_namespace == "crewai_flow:crewai-flow-router-id"
