"""Agent compositor: Run."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.agent import Agent
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


class TestRun:
    """Tests for Agent.run() method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    async def test_starts_then_runs_forever(
        self, mock_runtime, mock_adapter, kwargs, installs
    ):
        """run() hands the host's signal choice to run_forever."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run(**kwargs)

        mock_runtime.start.assert_awaited_once()
        mock_runtime.run_forever.assert_awaited_once_with(
            install_signal_handlers=installs
        )

    @pytest.mark.asyncio
    async def test_stops_on_completion(self, mock_runtime, mock_adapter):
        """Should stop runtime after run_forever completes."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run()

        mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stops_on_exception(self, mock_runtime, mock_adapter):
        """Should stop runtime even if run_forever raises."""
        mock_runtime.run_forever = AsyncMock(side_effect=Exception("Connection lost"))
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        with pytest.raises(Exception, match="Connection lost"):
            await agent.run()

        # Should still have called stop
        mock_runtime.stop.assert_awaited_once()
