"""Test orchestrator context_id handling.

Tests that the orchestrator's call_peer_agent tool properly extracts
context_id from LangGraph's RunnableConfig and passes it to the
gateway client, ensuring conversation continuity.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from tests.loaders import load_script_module

# Set dummy OpenAI API key for testing (model won't be called)
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-unit-tests")

agent = load_script_module(
    "examples/a2a_gateway/demo_orchestrator/agent.py", "demo_orchestrator_agent"
)
call_peer_agent = agent.call_peer_agent
set_gateway_client = agent.set_gateway_client


class TestOrchestratorContextId:
    """Tests that orchestrator passes context_id to gateway."""

    @pytest.fixture
    def mock_gateway_client(self) -> AsyncMock:
        """Create mock gateway client that tracks call_peer arguments."""
        client = AsyncMock()
        client.call_peer = AsyncMock(return_value="Success from peer")
        return client

    @pytest.mark.asyncio
    async def test_call_peer_agent_passes_context_id_from_config(
        self, mock_gateway_client: AsyncMock
    ) -> None:
        """call_peer_agent should extract context_id from RunnableConfig and pass to gateway.

        This is the key test that verifies the bug fix:
        - LangGraph passes thread_id in config["configurable"]["thread_id"]
        - call_peer_agent should extract this and pass it as context_id to call_peer
        """
        set_gateway_client(mock_gateway_client)

        # LangGraph passes thread_id in config["configurable"]["thread_id"]
        config = {"configurable": {"thread_id": "ctx-user-123"}}

        await call_peer_agent.ainvoke(
            {"peer_id": "weather-agent", "message": "Hello"},
            config=config,
        )

        # Verify context_id was passed to call_peer
        mock_gateway_client.call_peer.assert_called_once()
        call_kwargs = mock_gateway_client.call_peer.call_args.kwargs
        assert call_kwargs.get("context_id") == "ctx-user-123", (
            f"Expected context_id='ctx-user-123', got {call_kwargs.get('context_id')!r}. "
            f"This means the orchestrator is NOT preserving contextId from the user's request."
        )

    @pytest.mark.asyncio
    async def test_same_context_id_used_for_multiple_calls(
        self, mock_gateway_client: AsyncMock
    ) -> None:
        """Multiple tool calls with same config should use same context_id."""
        set_gateway_client(mock_gateway_client)
        config = {"configurable": {"thread_id": "ctx-session-456"}}

        # Simulate two tool calls in same conversation
        await call_peer_agent.ainvoke(
            {"peer_id": "weather-agent", "message": "First question"},
            config=config,
        )
        await call_peer_agent.ainvoke(
            {"peer_id": "data-agent", "message": "Second question"},
            config=config,
        )

        # Both calls should use same context_id
        assert mock_gateway_client.call_peer.call_count == 2
        for call in mock_gateway_client.call_peer.call_args_list:
            assert call.kwargs.get("context_id") == "ctx-session-456", (
                f"Expected all calls to use context_id='ctx-session-456', "
                f"got {call.kwargs.get('context_id')!r}"
            )

    @pytest.mark.asyncio
    async def test_call_peer_agent_handles_missing_config(
        self, mock_gateway_client: AsyncMock
    ) -> None:
        """call_peer_agent should handle missing config gracefully."""
        set_gateway_client(mock_gateway_client)

        # Call without config (context_id should be None)
        await call_peer_agent.ainvoke(
            {"peer_id": "weather-agent", "message": "Hello"},
        )

        # Should still work, gateway will generate its own context_id
        mock_gateway_client.call_peer.assert_called_once()
        # Just verify it doesn't crash - context_id may or may not be present

    @pytest.mark.asyncio
    async def test_call_peer_agent_handles_empty_configurable(
        self, mock_gateway_client: AsyncMock
    ) -> None:
        """call_peer_agent should handle config without thread_id."""
        set_gateway_client(mock_gateway_client)

        # Call with config but no thread_id
        config = {"configurable": {}}
        await call_peer_agent.ainvoke(
            {"peer_id": "weather-agent", "message": "Hello"},
            config=config,
        )

        # Should still work
        mock_gateway_client.call_peer.assert_called_once()
