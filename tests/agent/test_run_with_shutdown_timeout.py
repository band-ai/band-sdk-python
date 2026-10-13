"""Agent compositor: RunWithShutdownTimeout."""

from __future__ import annotations

import pytest

from band.agent import DEFAULT_SHUTDOWN_TIMEOUT, Agent


class TestRunWithShutdownTimeout:
    """Tests for Agent.run() with shutdown_timeout."""

    @pytest.mark.asyncio
    async def test_run_uses_default_timeout(self, mock_runtime, mock_adapter):
        """run() should use default shutdown timeout."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run()

        mock_runtime.stop.assert_awaited_once_with(timeout=DEFAULT_SHUTDOWN_TIMEOUT)

    @pytest.mark.asyncio
    async def test_run_uses_custom_timeout(self, mock_runtime, mock_adapter):
        """run() should use custom shutdown timeout."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run(shutdown_timeout=60.0)

        mock_runtime.stop.assert_awaited_once_with(timeout=60.0)

    @pytest.mark.asyncio
    async def test_run_with_none_timeout(self, mock_runtime, mock_adapter):
        """run(shutdown_timeout=None) should stop immediately."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run(shutdown_timeout=None)

        mock_runtime.stop.assert_awaited_once_with(timeout=None)
