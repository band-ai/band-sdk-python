"""Agent compositor: OnExecute."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.agent import Agent
from band.core.types import AgentInput


class TestOnExecute:
    """Tests for Agent._on_execute() method."""

    @pytest.mark.asyncio
    async def test_processes_event_with_preprocessor(
        self, mock_runtime, mock_adapter, mock_preprocessor
    ):
        """Should pass event to preprocessor."""
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        mock_ctx = MagicMock()
        mock_event = MagicMock()

        await agent._on_execute(mock_ctx, mock_event)

        mock_preprocessor.process.assert_awaited_once_with(
            ctx=mock_ctx,
            event=mock_event,
            agent_id="agent-123",
        )

    @pytest.mark.asyncio
    async def test_skips_when_preprocessor_returns_none(
        self, mock_runtime, mock_adapter, mock_preprocessor
    ):
        """Should not call adapter when preprocessor returns None."""
        mock_preprocessor.process.return_value = None
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        mock_ctx = MagicMock()
        mock_event = MagicMock()

        await agent._on_execute(mock_ctx, mock_event)

        mock_adapter.on_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_adapter_on_event(
        self, mock_runtime, mock_adapter, mock_preprocessor
    ):
        """Should call adapter.on_event with AgentInput."""
        mock_input = MagicMock(spec=AgentInput)
        mock_preprocessor.process.return_value = mock_input

        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        mock_ctx = MagicMock()
        mock_event = MagicMock()

        await agent._on_execute(mock_ctx, mock_event)

        mock_adapter.on_event.assert_awaited_once_with(mock_input)
