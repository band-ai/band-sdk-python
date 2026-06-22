"""Integration tests for basic agent operations.

Validates core agent operations (identity, peers, chat creation,
messaging, agent-to-agent communication) using the pre-existing
agents defined in .env.test.  No Enterprise plan required.

Run with: uv run pytest tests/integration/test_dynamic_agent.py -v -s

Prerequisites:
- BAND_API_KEY / TEST_AGENT_ID  (agent 1)
- BAND_API_KEY_2 / TEST_AGENT_ID_2  (agent 2)

History:
- TestDynamicAgentWorkflow replaced the old dynamic-agent flow that required
  Enterprise plan (Human API register_my_agent / delete_my_agent).
- TestUserAgentManagement (list_my_agents, list_my_peers) was removed because
  it exercised the Human API which requires Enterprise plan access. User API
  coverage belongs in the platform's own test suite, not the SDK.
- test_letta_live.py was removed because the Letta adapter is deprecated and
  scheduled for removal. The Letta SDK's Conversations API changed upstream,
  making the live tests unmaintainable.
"""

from __future__ import annotations

import logging

import pytest
from band_rest import ChatEventRequest, ChatMessageRequest
from band_rest.types import ChatMessageRequestMentionsItem as Mention

from tests.integration.conftest import (
    requires_api,
    requires_multi_agent,
)

logger = logging.getLogger(__name__)


@requires_api
class TestAgentWorkflow:
    """Test core agent operations using pre-existing agents from .env.test."""

    async def test_agent_can_fetch_identity(
        self,
        api_client,
        shared_agent1_info,
    ):
        """Verify the agent can authenticate and fetch its identity."""
        response = await api_client.agent_api_identity.get_agent_me()

        assert response.data is not None
        assert response.data.id == shared_agent1_info.id
        assert response.data.name == shared_agent1_info.name
        logger.info("Agent verified: %s (ID: %s)", response.data.name, response.data.id)

    async def test_agent_can_list_peers(self, api_client):
        """Verify the agent can list available peers."""
        response = await api_client.agent_api_peers.list_agent_peers()

        assert response.data is not None
        logger.info("Agent can see %s peers", len(response.data))

    async def test_agent_can_send_message_to_shared_room(
        self,
        api_client,
        shared_room,
        shared_user_peer,
    ):
        """Verify the agent can send a message to an existing chat room."""
        if shared_room is None:
            pytest.skip("shared_room not available")
        if shared_user_peer is None:
            pytest.skip("No User peer available for mention")

        msg_response = await api_client.agent_api_messages.create_agent_chat_message(
            shared_room,
            message=ChatMessageRequest(
                content=f"Integration test message for @{shared_user_peer.name}",
                mentions=[Mention(id=shared_user_peer.id, name=shared_user_peer.name)],
            ),
        )
        assert msg_response.data.id is not None
        logger.info("Sent message: %s to room %s", msg_response.data.id, shared_room)

    async def test_full_messaging_workflow(
        self,
        api_client,
        shared_room,
        shared_user_peer,
    ):
        """Test complete messaging flow: message + thought event."""
        if shared_room is None:
            pytest.skip("shared_room not available")
        if shared_user_peer is None:
            pytest.skip("No User peer available for mention")

        chat_id = shared_room
        peer_id = shared_user_peer.id
        peer_name = shared_user_peer.name

        # 1. Send message
        msg_response = await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=f"Hello @{peer_name} from integration test!",
                mentions=[Mention(id=peer_id, name=peer_name)],
            ),
        )
        assert msg_response.data.id is not None
        logger.info("Sent message: %s", msg_response.data.id)

        # 2. Send thought event
        event_response = await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Agent is thinking...",
                message_type="thought",
            ),
        )
        assert event_response.data.id is not None
        logger.info("Sent thought event: %s", event_response.data.id)

        logger.info("Full messaging workflow passed for room %s", chat_id)

    @requires_multi_agent
    async def test_agent_to_agent_communication(
        self,
        api_client,
        api_client_2,
        shared_multi_agent_room,
        shared_agent1_info,
        shared_agent2_info,
    ):
        """Test agent-to-agent messaging in a shared multi-agent room.

        Verifies that:
        1. Both agents are participants in the room
        2. Agent 1 can send a message mentioning Agent 2
        3. Agent 1 can send a thought event
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        agent1_id = shared_agent1_info.id
        agent2_id = shared_agent2_info.id
        agent2_name = shared_agent2_info.name

        # 1. Verify participants include both agents
        participants_response = (
            await api_client.agent_api_participants.list_agent_chat_participants(
                chat_id
            )
        )
        participant_ids = [p.id for p in participants_response.data]
        assert agent1_id in participant_ids, "Agent 1 not in chat"
        assert agent2_id in participant_ids, "Agent 2 not in chat"
        logger.info(
            "Verified both agents are participants: %s total",
            len(participants_response.data),
        )

        # 2. Send message from Agent 1 mentioning Agent 2
        msg_content = (
            f"Hello @{agent2_name}! This is an agent-to-agent communication test."
        )
        msg_response = await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=msg_content,
                mentions=[Mention(id=agent2_id, name=agent2_name)],
            ),
        )
        assert msg_response.data.id is not None
        logger.info("Sent message: %s", msg_response.data.id)

        # 3. Send a thought event (simulating agent processing)
        event_response = await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Processing agent-to-agent communication...",
                message_type="thought",
            ),
        )
        assert event_response.data.id is not None
        logger.info("Sent thought event: %s", event_response.data.id)

        logger.info(
            "Agent-to-agent communication test passed: %s -> %s",
            shared_agent1_info.name,
            agent2_name,
        )
