"""PlatformRuntime run_forever."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.runtime.platform_runtime import PlatformRuntime
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


class TestRunForever:
    """Tests for PlatformRuntime.run_forever() method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    async def test_delegates_to_link(self, mock_link, mock_runtime, kwargs, installs):
        """run_forever hands the host's signal choice to the link."""
        with patch("band.runtime.platform_runtime.BandLink") as mock_link_class:
            mock_link_class.return_value = mock_link
            with patch(
                "band.runtime.platform_runtime.AgentRuntime"
            ) as mock_runtime_class:
                mock_runtime_class.return_value = mock_runtime

                runtime = PlatformRuntime(
                    agent_id="agent-123",
                    api_key="test-key",
                )

                await runtime.start(on_execute=AsyncMock())
                await runtime.run_forever(**kwargs)

                mock_link.run_forever.assert_awaited_once_with(
                    install_signal_handlers=installs
                )

    @pytest.mark.asyncio
    async def test_run_forever_before_start_is_safe(self):
        """Should handle run_forever before start gracefully."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        # Should not raise (just does nothing)
        await runtime.run_forever()
