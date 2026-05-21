"""Integration tests for Agent contact event handling.

These tests require real API access and are skipped in CI.

Note: TestAgentHubRoomFlow was removed because HUB_ROOM strategy creates a
dedicated chat room at startup, which accumulates orphaned rooms across runs
(no delete API). The HUB_ROOM code path is covered by unit tests in
tests/runtime/test_contact_handler.py; live validation is deferred until the
platform supports room deletion.
"""

import asyncio
import os

import pytest

from thenvoi.agent import Agent
from thenvoi.adapters.pydantic_ai import PydanticAIAdapter
from thenvoi.platform.event import (
    ContactEvent,
)
from thenvoi.runtime.contact_tools import ContactTools
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

# Skip all tests if no API key is set
pytestmark = pytest.mark.skipif(
    os.getenv("THENVOI_API_KEY") is None,
    reason="THENVOI_API_KEY not set",
)


@pytest.fixture
def api_key():
    """Get API key from environment."""
    return os.environ["THENVOI_API_KEY"]


@pytest.fixture
def agent_id():
    """Get agent ID from environment."""
    agent_id = os.getenv("THENVOI_AGENT_ID") or os.getenv("TEST_AGENT_ID")
    if not agent_id:
        pytest.skip("THENVOI_AGENT_ID or TEST_AGENT_ID not set")
    return agent_id


@pytest.fixture
def ws_url():
    """Get WebSocket URL from environment."""
    return os.getenv("THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")


@pytest.fixture
def rest_url():
    """Get REST URL from environment."""
    return os.getenv(
        "THENVOI_BASE_URL", os.getenv("THENVOI_REST_URL", "https://app.band.ai")
    )


class TestAgentCallbackFlow:
    """Integration tests for Agent with CALLBACK strategy."""

    @pytest.mark.asyncio
    async def test_agent_callback_receives_events(
        self, api_key, agent_id, ws_url, rest_url
    ):
        """Agent with CALLBACK should receive contact events."""
        received_events: list[ContactEvent] = []

        async def capture_event(event: ContactEvent, tools: ContactTools) -> None:
            received_events.append(event)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=capture_event,
        )

        adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
        agent = Agent.create(
            adapter=adapter,
            agent_id=agent_id,
            api_key=api_key,
            ws_url=ws_url,
            rest_url=rest_url,
            contact_config=config,
        )

        # Start agent briefly to verify subscription works
        async with agent:
            # Give it a moment to connect
            await asyncio.sleep(1.0)

            # Verify subscription is active
            assert agent.is_contacts_subscribed is True
            assert agent.contact_config.strategy == ContactEventStrategy.CALLBACK

        # After stop, should be unsubscribed
        assert agent.is_contacts_subscribed is False


class TestAgentBroadcastFlow:
    """Integration tests for Agent with broadcast_changes."""

    @pytest.mark.asyncio
    async def test_agent_broadcast_with_disabled_strategy(
        self, api_key, agent_id, ws_url, rest_url
    ):
        """Agent with DISABLED + broadcast should subscribe to contacts."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )

        adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
        agent = Agent.create(
            adapter=adapter,
            agent_id=agent_id,
            api_key=api_key,
            ws_url=ws_url,
            rest_url=rest_url,
            contact_config=config,
        )

        async with agent:
            await asyncio.sleep(1.0)

            # Even with DISABLED, should subscribe because broadcast is enabled
            assert agent.is_contacts_subscribed is True
            assert agent.contact_config.strategy == ContactEventStrategy.DISABLED
            assert agent.contact_config.broadcast_changes is True


class TestAgentGracefulShutdown:
    """Integration tests for Agent graceful shutdown with contacts."""

    @pytest.mark.asyncio
    async def test_agent_graceful_shutdown_unsubscribes(
        self, api_key, agent_id, ws_url, rest_url
    ):
        """Agent.stop() should cleanly unsubscribe from contacts."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=lambda e, t: None,
        )

        adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
        agent = Agent.create(
            adapter=adapter,
            agent_id=agent_id,
            api_key=api_key,
            ws_url=ws_url,
            rest_url=rest_url,
            contact_config=config,
        )

        await agent.start()
        # Give the contacts channel time to subscribe
        await asyncio.sleep(3.0)
        assert agent.is_contacts_subscribed is True

        # Stop the agent — may not be fully graceful if the LLM adapter is
        # mid-processing messages from the shared room, but contacts should
        # still be unsubscribed.
        await agent.stop(timeout=10.0)

        assert agent.is_contacts_subscribed is False
        assert agent.is_running is False
