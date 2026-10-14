"""Agent compositor: Properties."""

from __future__ import annotations

from band.agent import Agent


class TestProperties:
    """Tests for Agent properties."""

    def test_runtime_property(self, mock_runtime, mock_adapter):
        """Should expose runtime property."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.runtime is mock_runtime

    def test_agent_name_property(self, mock_runtime, mock_adapter):
        """Should delegate agent_name to runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.agent_name == "TestBot"

    def test_agent_description_property(self, mock_runtime, mock_adapter):
        """Should delegate agent_description to runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.agent_description == "A test bot"
