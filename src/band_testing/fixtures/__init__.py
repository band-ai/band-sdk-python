"""Pytest fixtures for Band testing.

This module provides pytest fixtures that are loaded by the SDK test suite.

Core fixtures (always available):
- fake_agent_tools: FakeAgentTools instance for testing adapters
- mock_websocket: AsyncMock WebSocket client for subscription tests
- factory: MockDataFactory instance for creating test data

Mock API client fixtures (for unit tests):
- mock_agent_api: MagicMock of agent_api namespace
- mock_human_api: MagicMock of human_api namespace
- mock_api_client: AsyncMock with both APIs attached

Real API client fixtures (for integration tests):
- api_client: Real RestClient (returns None if no API key)

Sample message fixtures:
- sample_room_message: MessageCreatedPayload from a user
- sample_agent_message: MessageCreatedPayload from an agent

Usage:
    # Load pytest_plugins = ["band_testing.fixtures"] and fixtures are available
    def test_my_adapter(fake_agent_tools):
        await adapter.on_message(msg, fake_agent_tools, ...)
        assert len(fake_agent_tools.messages_sent) == 1

    def test_data_factory(factory):
        agent = factory.agent_me(id="test-123")
        assert agent.id == "test-123"

    async def test_api_calls(mock_api_client, mock_agent_api):
        mock_agent_api.get_agent_me.return_value = factory.response(...)
        await some_function(mock_api_client)
        mock_agent_api.get_agent_me.assert_called_once()

    def test_integration(api_client):
        if api_client is None:
            pytest.skip("API key not configured")
        response = api_client.agent_api.get_agent_me()
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from band_testing.factories import factory as _factory
from band_testing.fakes import FakeAgentTools

# Import API client fixtures - they will be auto-registered by pytest
from band_testing.fixtures.api_clients import (  # noqa: F401
    api_client,
    mock_agent_api,
    mock_api_client,
    mock_human_api,
)
from band_testing.streaming import (
    Mention,
    MessageCreatedPayload,
    MessageMetadata,
)


@pytest.fixture
def fake_agent_tools() -> FakeAgentTools:
    """Provide a FakeAgentTools instance for adapter testing.

    Tracks all calls to send_message, send_event, add_participant, etc.
    allowing assertions on tool usage without mocking.

    Example:
        async def test_adapter_sends_message(fake_agent_tools):
            adapter = MyAdapter()
            await adapter.process(message, fake_agent_tools)

            assert len(fake_agent_tools.messages_sent) == 1
            assert fake_agent_tools.messages_sent[0]["content"] == "Hello!"
    """
    return FakeAgentTools()


@pytest.fixture
def mock_websocket() -> AsyncMock:
    """Provide a mock WebSocket client for subscription tests.

    The mock is configured as an async context manager with common
    channel operations pre-configured.

    Example:
        async def test_subscription(mock_websocket):
            async with mock_websocket:
                await mock_websocket.join_chat_room_channel("room-123")
            mock_websocket.join_chat_room_channel.assert_called_once()
    """
    ws = AsyncMock()

    # Make it work as async context manager
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)

    # Mock channel operations
    ws.join_chat_room_channel = AsyncMock()
    ws.leave_chat_room_channel = AsyncMock()
    ws.join_agent_rooms_channel = AsyncMock()

    return ws


@pytest.fixture
def factory() -> Any:
    """Provide MockDataFactory for creating test objects.

    Example:
        def test_with_factory(factory):
            agent = factory.agent_me(id="test-agent", name="TestBot")
            message = factory.chat_message(content="Hello")
            response = factory.response(agent)
    """
    return _factory


# =============================================================================
# Sample Message Fixtures
# =============================================================================


@pytest.fixture
def sample_room_message() -> MessageCreatedPayload:
    """Standard test message from a user.

    Returns a MessageCreatedPayload representing a user message
    that mentions "TestBot" (agent-123).

    Properties:
    - id: "msg-789"
    - content: "@TestBot hello"
    - sender_id: "user-456"
    - sender_type: "User"
    - chat_room_id: "room-123"
    - metadata: includes mention of TestBot

    Example:
        def test_message_handling(sample_room_message):
            assert sample_room_message.sender_type == "User"
            assert "TestBot" in sample_room_message.content
    """
    return MessageCreatedPayload(
        id="msg-789",
        content="@TestBot hello",
        message_type="text",
        metadata=MessageMetadata(
            mentions=[Mention(id="agent-123", username="TestBot")], status="sent"
        ),
        sender_id="user-456",
        sender_type="User",
        chat_room_id="room-123",
        inserted_at="test-timestamp",
        updated_at="test-timestamp",
    )


@pytest.fixture
def sample_agent_message() -> MessageCreatedPayload:
    """Message from an agent (for filtering tests).

    Returns a MessageCreatedPayload representing a message sent
    BY the agent (agent-123). Useful for testing that handlers
    correctly filter out their own messages.

    Properties:
    - id: "msg-999"
    - content: "@TestBot hi"
    - sender_id: "agent-123"
    - sender_type: "Agent"
    - chat_room_id: "room-123"

    Example:
        def test_ignores_own_messages(sample_agent_message, handler):
            # Handler should skip messages from itself
            result = handler.should_process(sample_agent_message)
            assert result is False
    """
    return MessageCreatedPayload(
        id="msg-999",
        content="@TestBot hi",
        message_type="text",
        metadata=MessageMetadata(
            mentions=[Mention(id="agent-123", username="TestBot")], status="sent"
        ),
        sender_id="agent-123",
        sender_type="Agent",
        chat_room_id="room-123",
        inserted_at="test-timestamp",
        updated_at="test-timestamp",
    )
