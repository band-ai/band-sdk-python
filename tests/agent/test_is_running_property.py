"""Agent compositor: IsRunningProperty."""

from __future__ import annotations

import pytest

from band.agent import Agent


class TestIsRunningProperty:
    """Tests for Agent.is_running property."""

    def test_is_running_false_initially(self, mock_runtime, mock_adapter):
        """Should return False before start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.is_running is False

    @pytest.mark.asyncio
    async def test_is_running_true_after_start(self, mock_runtime, mock_adapter):
        """Should return True after start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()
        assert agent.is_running is True

    @pytest.mark.asyncio
    async def test_is_running_false_after_stop(self, mock_runtime, mock_adapter):
        """Should return False after stop()."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()
        await agent.stop()
        assert agent.is_running is False
