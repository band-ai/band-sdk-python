"""Tests for PlatformRuntime contact setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.types import ContactEventConfig, ContactEventStrategy


class TestPlatformRuntimeContactSetup:
    """Tests for PlatformRuntime contact setup."""

    @pytest.mark.asyncio
    async def test_setup_subscribes_with_callback_strategy(self):
        """PlatformRuntime should subscribe to contacts with CALLBACK strategy."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=AsyncMock(),
        )

        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        # Mock the link
        mock_link = MagicMock()
        mock_link.subscribe_agent_contacts = AsyncMock()
        runtime._link = mock_link

        # Mock the internal runtime
        mock_internal = MagicMock()
        mock_internal.presence = MagicMock()
        runtime._runtime = mock_internal

        # Call setup directly
        await runtime._setup_contact_handling(runtime._link, runtime._runtime)

        # Verify subscription
        mock_link.subscribe_agent_contacts.assert_called_once_with("test-agent-id")
        assert runtime.is_contacts_subscribed is True

    @pytest.mark.asyncio
    async def test_setup_subscribes_with_hub_room_strategy(self):
        """PlatformRuntime should subscribe to contacts with HUB_ROOM strategy."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )

        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        mock_link = MagicMock()
        mock_link.subscribe_agent_contacts = AsyncMock()
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

        mock_internal = MagicMock()
        mock_internal.presence = MagicMock()
        mock_internal.executions = {}
        runtime._runtime = mock_internal

        await runtime._setup_contact_handling(runtime._link, runtime._runtime)

        mock_link.subscribe_agent_contacts.assert_called_once()
        assert runtime.is_contacts_subscribed is True
        # Verify hub room was created at startup
        mock_link.rest.agent_api_chats.create_agent_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_skips_subscription_when_disabled(self):
        """PlatformRuntime should NOT subscribe with DISABLED and no broadcast."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=False,
        )

        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        mock_link = MagicMock()
        mock_link.subscribe_agent_contacts = AsyncMock()
        runtime._link = mock_link

        await runtime._setup_contact_handling(runtime._link, runtime._runtime)

        mock_link.subscribe_agent_contacts.assert_not_called()
        assert runtime.is_contacts_subscribed is False

    @pytest.mark.asyncio
    async def test_setup_subscribes_when_disabled_with_broadcast(self):
        """PlatformRuntime should subscribe with DISABLED + broadcast=True."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )

        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        mock_link = MagicMock()
        mock_link.subscribe_agent_contacts = AsyncMock()
        runtime._link = mock_link

        mock_internal = MagicMock()
        mock_internal.presence = MagicMock()
        runtime._runtime = mock_internal

        await runtime._setup_contact_handling(runtime._link, runtime._runtime)

        mock_link.subscribe_agent_contacts.assert_called_once()
        assert runtime.is_contacts_subscribed is True

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_when_subscribed(self):
        """PlatformRuntime.stop() should unsubscribe from contacts."""
        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
        )

        mock_link = MagicMock()
        mock_link.unsubscribe_agent_contacts = AsyncMock()
        mock_link.disconnect = AsyncMock()
        runtime._link = mock_link
        runtime._contacts_subscribed = True

        mock_internal = MagicMock()
        mock_internal.stop = AsyncMock(return_value=True)
        runtime._runtime = mock_internal

        await runtime.stop()

        mock_link.unsubscribe_agent_contacts.assert_called_once()
        assert runtime.is_contacts_subscribed is False

    @pytest.mark.asyncio
    async def test_stop_skips_unsubscribe_when_not_subscribed(self):
        """PlatformRuntime.stop() should not unsubscribe if not subscribed."""
        runtime = PlatformRuntime(
            agent_id="test-agent-id",
            api_key="test-api-key",
        )

        mock_link = MagicMock()
        mock_link.unsubscribe_agent_contacts = AsyncMock()
        mock_link.disconnect = AsyncMock()
        runtime._link = mock_link
        runtime._contacts_subscribed = False

        mock_internal = MagicMock()
        mock_internal.stop = AsyncMock(return_value=True)
        runtime._runtime = mock_internal

        await runtime.stop()

        mock_link.unsubscribe_agent_contacts.assert_not_called()
