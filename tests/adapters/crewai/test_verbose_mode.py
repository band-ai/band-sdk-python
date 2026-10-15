from __future__ import annotations

import pytest

from band.adapters.crewai import (
    CrewAIAdapterConfig,
)


class TestVerboseMode:
    @pytest.mark.asyncio
    async def test_verbose_mode_passed_to_agent(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter(CrewAIAdapterConfig(verbose=True))
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        assert call_kwargs["verbose"] is True
