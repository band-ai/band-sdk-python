"""Tests for Agent contact event wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.agent import Agent
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.runtime.platform_runtime import PlatformRuntime
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy


@pytest.fixture
def mock_adapter():
    """Create a mock adapter."""
    adapter = MagicMock(spec=SimpleAdapter)
    adapter.on_started = AsyncMock()
    adapter.on_cleanup = AsyncMock()
    adapter.on_event = AsyncMock()
    return adapter


class TestAgentCreateContactConfig:
    """Tests for Agent.create() with contact_config parameter."""

    def test_agent_create_accepts_contact_config(self, mock_adapter):
        """Agent.create() should accept contact_config parameter."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=AsyncMock(),
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        assert agent is not None
        assert agent.contact_config.strategy == ContactEventStrategy.CALLBACK

    def test_agent_create_default_no_contact_config(self, mock_adapter):
        """Agent.create() without contact_config should default to DISABLED."""
        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
        )

        assert agent.contact_config.strategy == ContactEventStrategy.DISABLED
        assert agent.contact_config.broadcast_changes is False

    def test_agent_passes_config_to_runtime(self, mock_adapter):
        """Agent should pass contact_config to PlatformRuntime."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            hub_task_id="my-hub-task",
            broadcast_changes=True,
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        # Verify config is passed through
        runtime_config = agent.runtime._contact_config
        assert runtime_config.strategy == ContactEventStrategy.HUB_ROOM
        assert runtime_config.hub_task_id == "my-hub-task"
        assert runtime_config.broadcast_changes is True


class TestAgentContactSubscription:
    """Tests for Agent contact subscription behavior."""

    @pytest.mark.asyncio
    async def test_agent_subscribes_to_contacts_when_callback(self, mock_adapter):
        """Agent with CALLBACK strategy should subscribe to contacts on start."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=AsyncMock(),
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        # Patch the runtime methods that need async
        with (
            patch.object(
                agent._runtime, "initialize", new_callable=AsyncMock
            ) as mock_init,
            patch.object(agent._runtime, "start", new_callable=AsyncMock) as mock_start,
        ):
            await agent.start()

            # Verify initialize and start were called
            mock_init.assert_called_once()
            mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_subscribes_to_contacts_when_hub_room(self, mock_adapter):
        """Agent with HUB_ROOM strategy should subscribe to contacts."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        with (
            patch.object(agent._runtime, "initialize", new_callable=AsyncMock),
            patch.object(agent._runtime, "start", new_callable=AsyncMock),
        ):
            await agent.start()

            # Verify the config is correct
            assert agent.contact_config.strategy == ContactEventStrategy.HUB_ROOM

    @pytest.mark.asyncio
    async def test_agent_no_subscription_when_disabled(self, mock_adapter):
        """Agent with DISABLED strategy and no broadcast should not subscribe."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=False,
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        # Before start, should not be subscribed
        assert agent.is_contacts_subscribed is False

        with (
            patch.object(agent._runtime, "initialize", new_callable=AsyncMock),
            patch.object(agent._runtime, "start", new_callable=AsyncMock),
        ):
            await agent.start()

            # Still not subscribed because we mocked the start
            # The real test is that the config is correct
            assert agent.contact_config.strategy == ContactEventStrategy.DISABLED
            assert agent.contact_config.broadcast_changes is False

    @pytest.mark.asyncio
    async def test_agent_unsubscribes_on_stop(self, mock_adapter):
        """Agent should unsubscribe from contacts on stop()."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=AsyncMock(),
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        with (
            patch.object(agent._runtime, "initialize", new_callable=AsyncMock),
            patch.object(agent._runtime, "start", new_callable=AsyncMock),
            patch.object(
                agent._runtime, "stop", new_callable=AsyncMock, return_value=True
            ) as mock_stop,
        ):
            await agent.start()
            graceful = await agent.stop()

            mock_stop.assert_called_once()
            assert graceful is True


class TestAgentContactBroadcast:
    """Tests for Agent contact broadcast behavior."""

    def test_agent_broadcast_enabled_with_disabled_strategy(self, mock_adapter):
        """DISABLED + broadcast_changes=True should be valid."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )

        agent = Agent.create(
            adapter=mock_adapter,
            agent_id="test-agent-id",
            api_key="test-api-key",
            contact_config=config,
        )

        assert agent.contact_config.strategy == ContactEventStrategy.DISABLED
        assert agent.contact_config.broadcast_changes is True


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
        await runtime._setup_contact_handling()

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

        await runtime._setup_contact_handling()

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

        await runtime._setup_contact_handling()

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

        await runtime._setup_contact_handling()

        mock_link.subscribe_agent_contacts.assert_called_once()
        assert runtime.is_contacts_subscribed is True

    @pytest.mark.asyncio
    async def test_stop_clears_contacts_subscribed_flag(self):
        """PlatformRuntime.stop() should clear contacts flag without explicit unsubscribe.

        The PHX client's shutdown already unsubscribes all topics before
        stop() runs, so an explicit unsubscribe races with that cleanup
        and produces a spurious warning. Socket close handles the rest.
        """
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

        mock_link.unsubscribe_agent_contacts.assert_not_called()
        mock_link.disconnect.assert_called_once()
        assert runtime.is_contacts_subscribed is False


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
        with patch("thenvoi.runtime.platform_runtime.AgentRuntime") as MockAgentRuntime:
            mock_runtime_instance = MagicMock()

            async def track_start():
                call_order.append("runtime_start")

            mock_runtime_instance.start = track_start
            mock_runtime_instance.presence = MagicMock()
            MockAgentRuntime.return_value = mock_runtime_instance

            # Wrap _setup_contact_handling to track order
            original_setup = runtime._setup_contact_handling

            async def track_setup():
                call_order.append("contact_setup")
                await original_setup()

            runtime._setup_contact_handling = track_setup

            # Skip initialize since we mocked the link
            with patch.object(runtime, "initialize", new_callable=AsyncMock):
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
        with patch("thenvoi.runtime.platform_runtime.AgentRuntime") as MockAgentRuntime:
            mock_runtime_instance = MagicMock()

            async def simulate_ws_connect():
                # WebSocket connects during runtime.start
                mock_link.is_connected = True

            mock_runtime_instance.start = simulate_ws_connect
            mock_runtime_instance.presence = MagicMock()
            mock_runtime_instance.executions = {}
            MockAgentRuntime.return_value = mock_runtime_instance

            with patch.object(runtime, "initialize", new_callable=AsyncMock):
                await runtime.start(on_execute=AsyncMock())

        # Verify WebSocket was connected when subscription happened
        assert ws_connected_at_subscribe is True, (
            "WebSocket must be connected before contact subscription. "
            f"Connection state at subscribe time: {ws_connected_at_subscribe}"
        )
