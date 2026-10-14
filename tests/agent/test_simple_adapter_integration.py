"""Agent compositor: SimpleAdapterIntegration."""

from __future__ import annotations

import pytest

from band.agent import Agent
from tests.agent.kit import MinimalAdapter


class TestSimpleAdapterIntegration:
    """Tests for SimpleAdapter integration."""

    @pytest.mark.asyncio
    async def test_works_with_simple_adapter(self, mock_runtime):
        """A SimpleAdapter subclass is started with the agent's identity."""
        adapter = MinimalAdapter()
        agent = Agent(runtime=mock_runtime, adapter=adapter)

        await agent.start()

        assert (adapter.agent_name, adapter.agent_description) == (
            "TestBot",
            "A test bot",
        )
