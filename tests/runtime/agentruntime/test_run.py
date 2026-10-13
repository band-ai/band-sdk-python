"""AgentRuntime.run()."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.runtime.runtime import AgentRuntime
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


class TestAgentRuntimeRun:
    """Test AgentRuntime.run() method."""

    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    async def test_run_starts_and_runs_forever(
        self, mock_link, mock_handler, kwargs, installs
    ):
        """run() hands the host's signal choice to link.run_forever()."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Mock start to track it was called
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock()

        await runtime.run(**kwargs)

        runtime.start.assert_called_once()
        mock_link.run_forever.assert_awaited_once_with(install_signal_handlers=installs)
        runtime.stop.assert_called_once()

    async def test_run_stops_on_error(self, mock_link, mock_handler):
        """run() should stop even if run_forever raises."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        mock_link.run_forever.side_effect = Exception("Connection lost")
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock()

        with pytest.raises(Exception, match="Connection lost"):
            await runtime.run()

        runtime.stop.assert_called_once()

    async def test_stop_wakes_run_when_host_owns_signals(self, mock_link, mock_handler):
        """stop() disconnects so run(install_signal_handlers=False) can return."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)
        runtime.presence.start = AsyncMock()
        runtime.presence.stop = AsyncMock()
        blocked = asyncio.Event()

        async def block_until_disconnected(**kwargs: object) -> None:
            await blocked.wait()

        async def disconnect_and_unblock() -> None:
            blocked.set()

        mock_link.run_forever.side_effect = block_until_disconnected
        mock_link.disconnect.side_effect = disconnect_and_unblock

        running = asyncio.create_task(runtime.run(install_signal_handlers=False))
        await asyncio.sleep(0)
        await runtime.stop()
        await asyncio.wait_for(running, timeout=1.0)

        mock_link.disconnect.assert_awaited()
        # Host stop() plus run()'s finally both tear down.
        assert runtime.presence.stop.await_count >= 1
