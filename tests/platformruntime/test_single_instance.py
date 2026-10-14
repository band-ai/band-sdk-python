"""PlatformRuntime single-instance guard."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import AgentAlreadyRunningError
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import AgentConfig


class TestSingleInstance:
    """One running process per agent id (see runtime/single_instance.py)."""

    @pytest.mark.asyncio
    async def test_second_start_of_same_agent_is_refused(self, platform_mocks):
        first = PlatformRuntime(agent_id="agent-123", api_key="k")
        second = PlatformRuntime(agent_id="agent-123", api_key="k")

        await first.start(on_execute=AsyncMock())
        with pytest.raises(AgentAlreadyRunningError, match="already running"):
            await second.start(on_execute=AsyncMock())
        await first.stop()

    @pytest.mark.asyncio
    async def test_stop_releases_the_agent_for_restart(self, platform_mocks):
        first = PlatformRuntime(agent_id="agent-123", api_key="k")
        await first.start(on_execute=AsyncMock())
        await first.stop()

        second = PlatformRuntime(agent_id="agent-123", api_key="k")
        await second.start(on_execute=AsyncMock())
        await second.stop()

    @pytest.mark.asyncio
    async def test_failed_start_does_not_hold_the_lock(
        self, platform_mocks, mock_runtime
    ):
        """A retry in the same process must not see its own dead lock."""
        mock_runtime.start.side_effect = [RuntimeError("boom"), None]
        runtime = PlatformRuntime(agent_id="agent-123", api_key="k")

        with pytest.raises(RuntimeError, match="boom"):
            await runtime.start(on_execute=AsyncMock())
        await runtime.start(on_execute=AsyncMock())
        await runtime.stop()

    @pytest.mark.asyncio
    async def test_opt_out_allows_duplicates(self, platform_mocks):
        config = AgentConfig(single_instance=False)
        first = PlatformRuntime(agent_id="agent-123", api_key="k", config=config)
        second = PlatformRuntime(agent_id="agent-123", api_key="k", config=config)
        await first.start(on_execute=AsyncMock())
        await second.start(on_execute=AsyncMock())
        await first.stop()
        await second.stop()
