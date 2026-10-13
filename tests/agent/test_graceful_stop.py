"""Agent compositor: GracefulStop."""

from __future__ import annotations

import pytest

from band.agent import Agent


class TestGracefulStop:
    """Tests for Agent.stop() with timeout."""

    @pytest.mark.asyncio
    async def test_stop_without_timeout(self, mock_runtime, mock_adapter):
        """stop() without timeout should pass None to runtime."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await agent.stop()

        mock_runtime.stop.assert_awaited_once_with(timeout=None)

    @pytest.mark.asyncio
    async def test_stop_with_timeout(self, mock_runtime, mock_adapter):
        """stop(timeout) should pass timeout to runtime."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await agent.stop(timeout=30.0)

        mock_runtime.stop.assert_awaited_once_with(timeout=30.0)

    @pytest.mark.asyncio
    async def test_stop_returns_graceful_status(self, mock_runtime, mock_adapter):
        """stop() should return graceful status from runtime."""
        mock_runtime.stop.return_value = False  # Cancelled mid-processing
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        result = await agent.stop(timeout=1.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_returns_true_when_not_started(self, mock_runtime, mock_adapter):
        """stop() should return True when not started."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        result = await agent.stop(timeout=5.0)

        assert result is True
        mock_runtime.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, mock_runtime, mock_adapter):
        """start() should be idempotent."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()
        await agent.start()  # Second call

        # Should only call runtime once
        assert mock_runtime.initialize.await_count == 1
