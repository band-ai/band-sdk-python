"""Tests for Agent.create() with contact_config parameter."""

from __future__ import annotations

from unittest.mock import AsyncMock

from band.agent import Agent
from band.runtime.types import ContactEventConfig, ContactEventStrategy


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
            hub_task_id="550e8400-e29b-41d4-a716-446655440000",
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
        assert runtime_config.hub_task_id == "550e8400-e29b-41d4-a716-446655440000"
        assert runtime_config.broadcast_changes is True
