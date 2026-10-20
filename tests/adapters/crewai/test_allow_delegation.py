from __future__ import annotations

import pytest

from band.adapters.crewai import CrewAIAdapterConfig


class TestAllowDelegation:
    @pytest.mark.asyncio
    async def test_allow_delegation_passed_to_agent(self, CrewAIAdapter, crewai_mocks):
        """allow_delegation parameter should be passed to CrewAI Agent."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(CrewAIAdapterConfig(allow_delegation=True))
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["allow_delegation"] is True

    @pytest.mark.asyncio
    async def test_allow_delegation_defaults_to_false(
        self, CrewAIAdapter, crewai_mocks
    ):
        """allow_delegation should default to False."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["allow_delegation"] is False
