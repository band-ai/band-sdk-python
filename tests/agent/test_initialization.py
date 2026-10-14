"""Agent compositor: Initialization."""

from __future__ import annotations

from band.agent import Agent
from band.preprocessing.default import DefaultPreprocessor


class TestInitialization:
    """Tests for Agent initialization."""

    def test_full_composition(self, mock_runtime, mock_adapter, mock_preprocessor):
        """Should accept runtime, adapter, and preprocessor."""
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        assert agent._runtime is mock_runtime
        assert agent._adapter is mock_adapter
        assert agent._preprocessor is mock_preprocessor

    def test_default_preprocessor(self, mock_runtime, mock_adapter):
        """Should use DefaultPreprocessor if none provided."""
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
        )

        assert isinstance(agent._preprocessor, DefaultPreprocessor)
