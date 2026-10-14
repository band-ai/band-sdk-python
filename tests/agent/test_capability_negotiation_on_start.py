"""Agent compositor: CapabilityNegotiationOnStart."""

from __future__ import annotations

import pytest

from band.agent import Agent
from band.core.types import Capability
from band.runtime.capabilities import FeatureFlag
from tests.agent.kit import FilesAdapter


class TestCapabilityNegotiationOnStart:
    """Agent.start() prunes capabilities the connected deployment doesn't serve."""

    @pytest.mark.asyncio
    async def test_files_capability_pruned_when_flag_off(self, mock_runtime):
        mock_runtime.feature_flags = {FeatureFlag.FILE_TRANSFER: False}
        adapter = FilesAdapter(capabilities=Capability.FILES)
        agent = Agent(runtime=mock_runtime, adapter=adapter)

        await agent.start()

        assert Capability.FILES not in adapter.features.capabilities

    @pytest.mark.asyncio
    async def test_files_capability_kept_when_flag_on(self, mock_runtime):
        mock_runtime.feature_flags = {FeatureFlag.FILE_TRANSFER: True}
        adapter = FilesAdapter(capabilities=Capability.FILES)
        agent = Agent(runtime=mock_runtime, adapter=adapter)

        await agent.start()

        assert Capability.FILES in adapter.features.capabilities
