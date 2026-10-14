"""PlatformRuntime stop."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.runtime.platform_runtime import PlatformRuntime


class TestStop:
    """Tests for PlatformRuntime.stop() method."""

    @pytest.mark.asyncio
    async def test_stops_runtime(self, mock_link, mock_runtime):
        """Should stop the AgentRuntime."""
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
                await runtime.stop()

                mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_link(self, mock_link, mock_runtime):
        """Should disconnect the BandLink."""
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
                await runtime.stop()

                mock_link.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_even_when_runtime_stop_fails(
        self, mock_link, mock_runtime
    ):
        """A failed AgentRuntime.stop still disconnects so run_forever can end."""
        mock_runtime.stop = AsyncMock(side_effect=RuntimeError("teardown failed"))
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
                with pytest.raises(RuntimeError, match="teardown failed"):
                    await runtime.stop()

                mock_link.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_even_when_contacts_unsubscribe_fails(
        self, mock_link, mock_runtime
    ):
        """A failed contacts unsubscribe still disconnects the link."""
        mock_link.unsubscribe_agent_contacts = AsyncMock(
            side_effect=RuntimeError("unsubscribe failed")
        )
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
                runtime._contacts_subscribed = True
                await runtime.stop()

                mock_link.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self):
        """Should handle stop before start gracefully."""
        runtime = PlatformRuntime(
            agent_id="agent-123",
            api_key="test-key",
        )

        # Should not raise
        await runtime.stop()
