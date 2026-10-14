"""TestStartupOrder"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import ContactEventConfig, ContactEventStrategy


class TestStartupOrder:
    """Tests for correct ordering of startup operations.

    These tests verify that contact subscription happens AFTER WebSocket connects.
    This prevents "RuntimeError: Not connected" when subscribing to contacts.
    """

    @pytest.mark.asyncio
    async def test_contact_subscription_after_runtime_start(self):
        """Contact subscription must happen AFTER runtime.start() connects WebSocket."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=AsyncMock(),
        )

        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        # Track call order
        call_order: list[str] = []

        # Mock initialize to set up link
        mock_link = MagicMock()
        mock_link.subscribe_agent_contacts = AsyncMock()
        runtime._link = mock_link
        runtime._agent_name = "Test Agent"
        runtime._agent_description = "Test"

        # Patch AgentRuntime to track when it starts
        with patch("band.runtime.platform_runtime.AgentRuntime") as MockAgentRuntime:
            mock_runtime_instance = MagicMock()

            async def track_start():
                call_order.append("runtime_start")

            mock_runtime_instance.start = track_start
            mock_runtime_instance.presence = MagicMock()
            MockAgentRuntime.return_value = mock_runtime_instance

            # Wrap _setup_contact_handling to track order
            original_setup = runtime._setup_contact_handling

            async def track_setup(*args):
                call_order.append("contact_setup")
                await original_setup(*args)

            runtime._setup_contact_handling = track_setup

            # initialize() returns the link already set above
            await runtime.start(on_execute=AsyncMock())

        # Verify order: runtime_start MUST come before contact_setup
        assert call_order == ["runtime_start", "contact_setup"], (
            f"Wrong order: {call_order}. "
            "Contact setup must happen AFTER runtime.start() connects WebSocket."
        )

    @pytest.mark.asyncio
    async def test_websocket_connected_before_contact_subscription(self):
        """Verify WebSocket is connected when contact subscription is attempted."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )

        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        # Track if WebSocket was "connected" when subscribe was called
        ws_connected_at_subscribe = None

        # Mock link that tracks connection state
        mock_link = MagicMock()
        mock_link.is_connected = False  # Start disconnected

        async def check_connection_on_subscribe(agent_id):
            nonlocal ws_connected_at_subscribe
            ws_connected_at_subscribe = mock_link.is_connected

        mock_link.subscribe_agent_contacts = check_connection_on_subscribe

        # Mock REST client for hub room creation
        mock_chat_response = MagicMock()
        mock_chat_response.data = MagicMock()
        mock_chat_response.data.id = "hub-room-123"
        mock_link.rest = MagicMock()
        mock_link.rest.agent_api_chats = MagicMock()
        mock_link.rest.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_chat_response
        )

        runtime._link = mock_link
        runtime._agent_name = "Test Agent"
        runtime._agent_description = "Test"

        # Patch AgentRuntime - its start() method connects WebSocket
        with patch("band.runtime.platform_runtime.AgentRuntime") as MockAgentRuntime:
            mock_runtime_instance = MagicMock()

            async def simulate_ws_connect():
                # WebSocket connects during runtime.start
                mock_link.is_connected = True

            mock_runtime_instance.start = simulate_ws_connect
            mock_runtime_instance.presence = MagicMock()
            mock_runtime_instance.executions = {}
            MockAgentRuntime.return_value = mock_runtime_instance

            await runtime.start(on_execute=AsyncMock())

        # Verify WebSocket was connected when subscription happened
        assert ws_connected_at_subscribe is True, (
            "WebSocket must be connected before contact subscription. "
            f"Connection state at subscribe time: {ws_connected_at_subscribe}"
        )
