"""Agent compositor: RunForever."""

from __future__ import annotations

import pytest

from band.agent import Agent
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


class TestRunForever:
    """Tests for Agent.run_forever() method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    async def test_run_forever_delegates_to_runtime(
        self, mock_runtime, mock_adapter, kwargs, installs
    ):
        """run_forever() hands the host's signal choice to the runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await agent.run_forever(**kwargs)

        mock_runtime.run_forever.assert_awaited_once_with(
            install_signal_handlers=installs
        )

    @pytest.mark.asyncio
    async def test_run_forever_works_with_context_manager(
        self, mock_runtime, mock_adapter
    ):
        """run_forever() should work inside async with block."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            await agent.run_forever()

        mock_runtime.run_forever.assert_awaited_once()
