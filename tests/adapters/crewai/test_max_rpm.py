from __future__ import annotations

import pytest

from band.adapters.crewai import CrewAIAdapterConfig


class TestMaxRpm:
    @pytest.mark.asyncio
    async def test_max_rpm_passed_to_agent(self, CrewAIAdapter, crewai_mocks):
        """max_rpm parameter should be passed to CrewAI Agent."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(CrewAIAdapterConfig(max_rpm=10))
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["max_rpm"] == 10

    @pytest.mark.asyncio
    async def test_max_rpm_defaults_to_none(self, CrewAIAdapter, crewai_mocks):
        """max_rpm should default to None (no rate limiting)."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["max_rpm"] is None
