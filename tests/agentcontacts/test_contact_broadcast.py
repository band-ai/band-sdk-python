"""Tests for Agent contact broadcast behavior."""

from __future__ import annotations

from band.agent import Agent
from band.runtime.types import ContactEventConfig, ContactEventStrategy


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
