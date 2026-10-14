"""Agent compositor: Start."""

from __future__ import annotations

import pytest

from band.agent import Agent


class TestStart:
    """Tests for Agent.start() method."""

    @pytest.mark.asyncio
    async def test_starts_runtime(self, mock_runtime, mock_adapter):
        """Should start the platform runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        mock_runtime.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_adapter_on_started(self, mock_runtime, mock_adapter):
        """Should call adapter.on_started with agent metadata."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        mock_adapter.on_started.assert_awaited_once_with("TestBot", "A test bot")

    @pytest.mark.asyncio
    async def test_passes_on_execute_to_runtime(self, mock_runtime, mock_adapter):
        """Should pass _on_execute to runtime.start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        call_kwargs = mock_runtime.start.call_args.kwargs
        assert call_kwargs["on_execute"] == agent._on_execute

    @pytest.mark.asyncio
    async def test_passes_on_cleanup_to_runtime(self, mock_runtime, mock_adapter):
        """Should pass adapter.on_cleanup to runtime.start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        call_kwargs = mock_runtime.start.call_args.kwargs
        assert call_kwargs["on_cleanup"] == mock_adapter.on_cleanup
