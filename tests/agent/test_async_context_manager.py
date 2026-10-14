"""Agent compositor: AsyncContextManager."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.agent import DEFAULT_SHUTDOWN_TIMEOUT, Agent


class TestAsyncContextManager:
    """Tests for Agent async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_starts_agent(self, mock_runtime, mock_adapter):
        """__aenter__ should start the agent."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            assert agent.is_running is True

    @pytest.mark.asyncio
    async def test_aenter_returns_agent(self, mock_runtime, mock_adapter):
        """__aenter__ should return the agent instance."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent as ctx:
            assert ctx is agent

    @pytest.mark.asyncio
    async def test_aexit_stops_agent(self, mock_runtime, mock_adapter):
        """__aexit__ should stop the agent."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            pass

        assert agent.is_running is False
        mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_uses_default_timeout(self, mock_runtime, mock_adapter):
        """__aexit__ should use default shutdown timeout."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            pass

        mock_runtime.stop.assert_awaited_once_with(timeout=DEFAULT_SHUTDOWN_TIMEOUT)

    @pytest.mark.asyncio
    async def test_aexit_uses_run_timeout(self, mock_runtime, mock_adapter):
        """__aexit__ should use the timeout from run() if set."""
        mock_runtime.stop.return_value = True
        mock_runtime.run_forever = AsyncMock(side_effect=KeyboardInterrupt())
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Use try/except since run() will raise KeyboardInterrupt
        try:
            await agent.run(shutdown_timeout=60.0)
        except KeyboardInterrupt:
            pass

        # The finally block in run() should use the custom timeout
        mock_runtime.stop.assert_awaited_once_with(timeout=60.0)

    @pytest.mark.asyncio
    async def test_aexit_respects_none_timeout_from_run(
        self, mock_runtime, mock_adapter
    ):
        """__aexit__ should use None if run() was called with shutdown_timeout=None."""
        mock_runtime.stop.return_value = True
        mock_runtime.run_forever = AsyncMock(side_effect=KeyboardInterrupt())
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Use try/except since run() will raise KeyboardInterrupt
        try:
            await agent.run(shutdown_timeout=None)
        except KeyboardInterrupt:
            pass

        # The finally block in run() should use None (immediate cancellation)
        mock_runtime.stop.assert_awaited_once_with(timeout=None)

    @pytest.mark.asyncio
    async def test_aexit_stops_on_exception(self, mock_runtime, mock_adapter):
        """__aexit__ should stop agent even on exception."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        with pytest.raises(ValueError):
            async with agent:
                raise ValueError("Test error")

        mock_runtime.stop.assert_awaited_once()
