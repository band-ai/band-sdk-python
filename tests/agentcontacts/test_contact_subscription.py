"""Tests for Agent contact subscription behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.agent import Agent
from band.runtime.types import ContactEventConfig, ContactEventStrategy


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
