"""PlatformRuntime incomplete cleanup ownership."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.exceptions import AgentAlreadyRunningError, CleanupIncompleteError
from band.runtime.platform_runtime import PlatformRuntime


class TestIncompleteCleanupOwnership:
    """The runtime and its guard stay held until cleanup actually succeeds."""

    @staticmethod
    def fail_cleanup(mock_runtime: MagicMock) -> None:
        mock_runtime.stop.side_effect = ExceptionGroup(
            "Room cleanup remains incomplete", [RuntimeError("room cleanup failed")]
        )

    @staticmethod
    def finish_cleanup(mock_runtime: MagicMock) -> None:
        mock_runtime.stop.side_effect = None

    @pytest.mark.asyncio
    async def test_incomplete_stop_keeps_guard_and_blocks_restart(
        self, platform_mocks, mock_runtime
    ):
        first = PlatformRuntime(agent_id="agent-123", api_key="k")
        second = PlatformRuntime(agent_id="agent-123", api_key="k")
        await first.start(on_execute=AsyncMock())
        self.fail_cleanup(mock_runtime)

        with pytest.raises(ExceptionGroup):
            await first.stop()
        with pytest.raises(AgentAlreadyRunningError):
            await second.start(on_execute=AsyncMock())
        with pytest.raises(CleanupIncompleteError):
            await first.start(on_execute=AsyncMock())

        self.finish_cleanup(mock_runtime)
        await first.stop()
        await second.start(on_execute=AsyncMock())
        await second.stop()

    @pytest.mark.asyncio
    async def test_failed_start_with_failed_unwind_keeps_guard(
        self, platform_mocks, mock_runtime
    ):
        mock_runtime.start.side_effect = RuntimeError("boom")
        self.fail_cleanup(mock_runtime)
        first = PlatformRuntime(agent_id="agent-123", api_key="k")

        with pytest.raises(RuntimeError, match="boom"):
            await first.start(on_execute=AsyncMock())
        with pytest.raises(AgentAlreadyRunningError):
            await PlatformRuntime(agent_id="agent-123", api_key="k").start(
                on_execute=AsyncMock()
            )
        self.finish_cleanup(mock_runtime)
        await first.stop()

    @pytest.mark.asyncio
    async def test_host_claimed_guard_is_released_only_by_the_host(
        self, platform_mocks
    ):
        hosted = PlatformRuntime(agent_id="agent-123", api_key="k")
        hosted.claim_single_instance()
        await hosted.start(on_execute=AsyncMock())
        await hosted.stop()

        second = PlatformRuntime(agent_id="agent-123", api_key="k")
        with pytest.raises(AgentAlreadyRunningError):
            await second.start(on_execute=AsyncMock())
        hosted.release_single_instance()
        await second.start(on_execute=AsyncMock())
        await second.stop()
