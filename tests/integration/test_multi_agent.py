"""Multi-agent integration tests.

Tests the interaction between multiple agents in the same chat room.
Validates that agents only see messages where they are @mentioned.
Validates that agents do NOT see other agents' events (thoughts, tool_calls, tool_results).

Setup:
- Agent 1: Primary test agent
- Agent 2: Secondary test agent
- User: Owner of both agents

Test scenarios:
1. Agent 1 sends message to User (no @mention of Agent 2) -> Agent 2 should NOT see it
2. Agent 1 creates events (thought, tool_call, tool_result) -> Agent 2 should NOT see them
3. WebSocket should NOT broadcast messages to agents that are not @mentioned

Run with: uv run pytest tests/integration/test_multi_agent.py -v -s
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from band_rest import ChatEventRequest, ChatMessageRequest
from band_rest.core.api_error import ApiError
from band_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
    ParticipantRequest,
)

from band.client.streaming import MessageCreatedPayload, WebSocketClient
from tests.integration.conftest import fetch_all_context, requires_multi_agent

logger = logging.getLogger(__name__)


@requires_multi_agent
class TestMultiAgentChatRoom:
    """Test multi-agent chat room interactions."""

    async def test_agent_does_not_see_messages_without_mention_via_context(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
        shared_user_peer,
        shared_agent1_info,
        shared_agent2_info,
    ):
        """Test that Agent 2 does NOT see messages in /context where it's not @mentioned."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Agent isolation via /context API")
        logger.info("=" * 60)

        agent1_id = shared_agent1_info.id
        agent1_name = shared_agent1_info.name
        agent2_id = shared_agent2_info.id
        logger.info("Agent 1: %s (ID: %s)", agent1_name, agent1_id)
        logger.info("Agent 2: %s (ID: %s)", shared_agent2_info.name, agent2_id)

        assert shared_user_peer is not None, "Need a User peer for this test"
        user_id = shared_user_peer.id
        user_name = shared_user_peer.name
        logger.info("User (owner): %s (ID: %s)", user_name, user_id)

        chat_id = shared_multi_agent_room

        # Verify all participants are in the chat
        response = await api_client.agent_api_participants.list_agent_chat_participants(
            chat_id
        )
        participants = response.data or []
        logger.info("\nChat participants (%s):", len(participants))
        for p in participants:
            logger.info("  - %s (%s, role: %s)", p.name, p.type, p.role)

        assert any(p.id == agent1_id for p in participants), "Agent 1 should be in chat"
        assert any(p.id == agent2_id for p in participants), "Agent 2 should be in chat"
        assert any(p.id == user_id for p in participants), "User should be in chat"

        # Agent 1 sends a message mentioning ONLY the User (not Agent 2)
        message_content = f"Hello @{user_name}, this message is just for you!"
        response = await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=message_content,
                mentions=[Mention(id=user_id, name=user_name)],
            ),
        )
        agent1_message_id = response.data.id
        logger.info("\nAgent 1 sent message (ID: %s)", agent1_message_id)
        logger.info("  Content: '%s'", message_content)
        logger.info("  Mentions: User only (NOT Agent 2)")

        # Give the platform time to process
        await asyncio.sleep(0.5)

        # Agent 2 queries /context for the chat
        logger.info("\nAgent 2 querying /context for chat %s...", chat_id)
        context = await fetch_all_context(api_client_2, chat_id)
        logger.info("Agent 2 received %s items in context", len(context))

        # Check if Agent 1's message is in Agent 2's context
        agent1_message_in_context = any(
            hasattr(item, "id") and item.id == agent1_message_id for item in context
        )

        logger.info(
            "\nAgent 1's message visible to Agent 2: %s", agent1_message_in_context
        )

        # ASSERTION: Agent 2 should NOT see Agent 1's message (no @mention of Agent 2)
        assert not agent1_message_in_context, (
            f"Agent 2 should NOT see Agent 1's message (ID: {agent1_message_id}) "
            f"because Agent 2 was not @mentioned"
        )

        logger.info("\n" + "=" * 60)
        logger.info("SUCCESS: Agent 2 correctly does NOT see Agent 1's message")
        logger.info("=" * 60)

    async def test_agent_does_not_receive_websocket_notification_without_mention(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
        shared_user_peer,
        shared_agent2_info,
    ):
        """Test that Agent 2's WebSocket does NOT receive messages where it's not @mentioned."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Backend WebSocket filtering (agent isolation)")
        logger.info("=" * 60)

        agent2_id = shared_agent2_info.id
        logger.info("Agent 2: %s (ID: %s)", shared_agent2_info.name, agent2_id)

        assert shared_user_peer is not None, "Need a User peer for this test"
        user_id = shared_user_peer.id
        user_name = shared_user_peer.name
        logger.info("User: %s (ID: %s)", user_name, user_id)

        chat_id = shared_multi_agent_room

        # Track messages received by Agent 2's raw WebSocket
        agent2_received_messages: list[MessageCreatedPayload] = []

        async def agent2_message_handler(payload: MessageCreatedPayload):
            logger.info(
                "  [Agent 2 WS] Received: %s from %s", payload.id, payload.sender_id
            )
            agent2_received_messages.append(payload)

        # Agent 2 connects to WebSocket and subscribes to the chat room
        agent2_ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key_2,
            agent_id=agent2_id,
        )

        async with agent2_ws:
            await agent2_ws.join_chat_room_channel(chat_id, agent2_message_handler)
            logger.info("Agent 2 subscribed to chat_room:%s", chat_id)

            await asyncio.sleep(0.3)

            # Agent 1 sends message mentioning ONLY the User (not Agent 2)
            message_content = f"@{user_name}, this is a private message for you!"
            response = await api_client.agent_api_messages.create_agent_chat_message(
                chat_id,
                message=ChatMessageRequest(
                    content=message_content,
                    mentions=[Mention(id=user_id, name=user_name)],
                ),
            )
            agent1_message_id = response.data.id
            logger.info("\nAgent 1 sent message (ID: %s)", agent1_message_id)
            logger.info("  Mentions: User only (NOT Agent 2)")

            # Wait to see if Agent 2 receives the message
            await asyncio.sleep(1.5)

        logger.info(
            "\nMessages received by Agent 2's raw WebSocket: %s",
            len(agent2_received_messages),
        )

        # Check if Agent 1's specific message was received
        agent1_message_received = any(
            m.id == agent1_message_id for m in agent2_received_messages
        )

        # ASSERTION: Agent 2's raw WebSocket should NOT receive Agent 1's message
        assert not agent1_message_received, (
            f"BACKEND BUG: Agent 2's WebSocket received Agent 1's message (ID: {agent1_message_id}) "
            f"but Agent 2 was NOT @mentioned. Platform should filter WebSocket broadcasts."
        )

        logger.info("\n" + "=" * 60)
        logger.info("SUCCESS: Backend correctly filtered WebSocket broadcast")
        logger.info("=" * 60)

    async def test_agent_sees_messages_with_own_mention(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
        shared_agent2_info,
    ):
        """Test that Agent 2 DOES see messages where it IS @mentioned."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Agent visibility when @mentioned")
        logger.info("=" * 60)

        agent2_id = shared_agent2_info.id
        agent2_name = shared_agent2_info.name
        logger.info("Agent 2: %s (ID: %s)", agent2_name, agent2_id)

        chat_id = shared_multi_agent_room

        # Agent 1 sends message mentioning Agent 2
        message_content = f"Hey @{agent2_name}, can you help with this?"
        response = await api_client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=message_content,
                mentions=[Mention(id=agent2_id, name=agent2_name)],
            ),
        )
        agent1_message_id = response.data.id
        logger.info(
            "\nAgent 1 sent message mentioning Agent 2 (ID: %s)", agent1_message_id
        )

        await asyncio.sleep(0.5)

        # Agent 2 queries /context
        context = await fetch_all_context(api_client_2, chat_id)
        logger.info("Agent 2 received %s items in context", len(context))

        # Check if Agent 1's message is visible
        agent1_message_visible = any(
            hasattr(item, "id") and item.id == agent1_message_id for item in context
        )

        logger.info("Agent 1's message visible to Agent 2: %s", agent1_message_visible)

        # ASSERTION: Agent 2 SHOULD see the message (it was @mentioned)
        assert agent1_message_visible, (
            f"Agent 2 SHOULD see Agent 1's message (ID: {agent1_message_id}) "
            f"because Agent 2 WAS @mentioned"
        )

        logger.info("\n" + "=" * 60)
        logger.info("SUCCESS: Agent 2 correctly sees message where it's @mentioned")
        logger.info("=" * 60)

    async def test_agent_does_not_see_other_agent_events_via_context(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
    ):
        """Test that Agent 2 does NOT see Agent 1's events (thoughts, tool_calls, tool_results)."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Event isolation between agents")
        logger.info("=" * 60)

        chat_id = shared_multi_agent_room

        # Agent 1 creates events: thought, tool_call, tool_result
        logger.info("\n--- Agent 1 creating events ---")

        # Thought event
        response = await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Analyzing the user's request...",
                message_type="thought",
            ),
        )
        thought_event_id = response.data.id
        logger.info("Agent 1 created thought event (ID: %s)", thought_event_id)

        # Tool call event
        tool_metadata = {
            "function": {
                "name": "search_database",
                "arguments": {"query": "test query"},
            }
        }
        response = await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Calling search_database",
                message_type="tool_call",
                metadata=tool_metadata,
            ),
        )
        tool_call_event_id = response.data.id
        logger.info("Agent 1 created tool_call event (ID: %s)", tool_call_event_id)

        # Tool result event
        result_metadata = {"result": {"found": 3, "items": ["a", "b", "c"]}}
        response = await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Search returned 3 results",
                message_type="tool_result",
                metadata=result_metadata,
            ),
        )
        tool_result_event_id = response.data.id
        logger.info("Agent 1 created tool_result event (ID: %s)", tool_result_event_id)

        await asyncio.sleep(0.5)

        # Agent 1 queries /context - should see its own events
        logger.info("\n--- Agent 1 querying /context ---")
        agent1_context = await fetch_all_context(api_client, chat_id)
        agent1_context_ids = {item.id for item in agent1_context if hasattr(item, "id")}
        logger.info("Agent 1 received %s items in context", len(agent1_context))

        assert thought_event_id in agent1_context_ids, (
            "Agent 1 should see its own thought event"
        )
        assert tool_call_event_id in agent1_context_ids, (
            "Agent 1 should see its own tool_call event"
        )
        assert tool_result_event_id in agent1_context_ids, (
            "Agent 1 should see its own tool_result event"
        )

        # Agent 2 queries /context - should NOT see Agent 1's events
        logger.info("\n--- Agent 2 querying /context ---")
        agent2_context = await fetch_all_context(api_client_2, chat_id)
        agent2_context_ids = {item.id for item in agent2_context if hasattr(item, "id")}
        logger.info("Agent 2 received %s items in context", len(agent2_context))

        assert thought_event_id not in agent2_context_ids, (
            f"Agent 2 should NOT see Agent 1's thought event (ID: {thought_event_id})"
        )
        assert tool_call_event_id not in agent2_context_ids, (
            f"Agent 2 should NOT see Agent 1's tool_call event (ID: {tool_call_event_id})"
        )
        assert tool_result_event_id not in agent2_context_ids, (
            f"Agent 2 should NOT see Agent 1's tool_result event (ID: {tool_result_event_id})"
        )

        logger.info("\n" + "=" * 60)
        logger.info("SUCCESS: Agent 2 correctly does NOT see Agent 1's events")
        logger.info("=" * 60)

    async def test_agent_sees_own_events_but_not_others(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
    ):
        """Test that each agent sees only their own events, not other agents' events."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Each agent sees only their own events")
        logger.info("=" * 60)

        chat_id = shared_multi_agent_room

        # Agent 1 creates a thought event
        response = await api_client.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Agent 1 is thinking...",
                message_type="thought",
            ),
        )
        agent1_thought_id = response.data.id
        logger.info("\nAgent 1 created thought (ID: %s)", agent1_thought_id)

        # Agent 2 creates a thought event
        response = await api_client_2.agent_api_events.create_agent_chat_event(
            chat_id,
            event=ChatEventRequest(
                content="Agent 2 is thinking...",
                message_type="thought",
            ),
        )
        agent2_thought_id = response.data.id
        logger.info("Agent 2 created thought (ID: %s)", agent2_thought_id)

        await asyncio.sleep(0.5)

        # Agent 1 queries /context
        logger.info("\n--- Agent 1 querying /context ---")
        agent1_context = await fetch_all_context(api_client, chat_id)
        agent1_context_ids = {item.id for item in agent1_context if hasattr(item, "id")}

        agent1_sees_own_thought = agent1_thought_id in agent1_context_ids
        agent1_sees_agent2_thought = agent2_thought_id in agent1_context_ids
        logger.info("Agent 1 sees own thought: %s", agent1_sees_own_thought)
        logger.info("Agent 1 sees Agent 2's thought: %s", agent1_sees_agent2_thought)

        # Agent 2 queries /context
        logger.info("\n--- Agent 2 querying /context ---")
        agent2_context = await fetch_all_context(api_client_2, chat_id)
        agent2_context_ids = {item.id for item in agent2_context if hasattr(item, "id")}

        agent2_sees_own_thought = agent2_thought_id in agent2_context_ids
        agent2_sees_agent1_thought = agent1_thought_id in agent2_context_ids
        logger.info("Agent 2 sees own thought: %s", agent2_sees_own_thought)
        logger.info("Agent 2 sees Agent 1's thought: %s", agent2_sees_agent1_thought)

        # Assertions
        assert agent1_sees_own_thought, "Agent 1 should see its own thought"
        assert not agent1_sees_agent2_thought, (
            "Agent 1 should NOT see Agent 2's thought"
        )
        assert agent2_sees_own_thought, "Agent 2 should see its own thought"
        assert not agent2_sees_agent1_thought, (
            "Agent 2 should NOT see Agent 1's thought"
        )

        logger.info("\n" + "=" * 60)
        logger.info("SUCCESS: Each agent sees only their own events")
        logger.info("=" * 60)

    async def test_member_agent_can_list_peers_and_add_participants(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
        shared_agent1_info,
        shared_agent2_info,
        shared_user_peer,
    ):
        """Test that a member agent can list peers and add participants to a chat.

        Uses the shared multi-agent room. Agent 2 is a member and attempts
        to add participants that are not yet in the room.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Member agent adding participants")
        logger.info("=" * 60)

        agent1_id = shared_agent1_info.id
        agent1_name = shared_agent1_info.name
        agent2_id = shared_agent2_info.id
        agent2_name = shared_agent2_info.name
        logger.info("Agent 1 (Owner): %s (ID: %s)", agent1_name, agent1_id)
        logger.info("Agent 2 (Member): %s (ID: %s)", agent2_name, agent2_id)

        chat_id = shared_multi_agent_room

        # Find peers not already in the room
        response = await api_client.agent_api_peers.list_agent_peers()
        all_peers = response.data or []

        # Find another Agent peer (not Agent 1 or Agent 2)
        other_agent = next(
            (
                p
                for p in all_peers
                if p.type == "Agent" and p.id not in [agent1_id, agent2_id]
            ),
            None,
        )

        # Agent 2 lists peers (should work)
        logger.info("\n--- Agent 2 lists peers ---")
        response = await api_client_2.agent_api_peers.list_agent_peers(
            not_in_chat=chat_id
        )
        peers = response.data or []
        logger.info("Agent 2 sees %s peers not in chat:", len(peers))
        for p in peers[:5]:
            logger.info("  - %s (%s)", p.name, p.type)
        if len(peers) > 5:
            logger.info("  ... and %s more", len(peers) - 5)

        # Agent 2 (member) tries to add another Agent if available
        if other_agent:
            other_agent_id = other_agent.id
            other_agent_name = other_agent.name
            logger.info(
                "\n--- Agent 2 (member) tries to add Agent %s ---", other_agent_name
            )
            add_agent_success = False
            try:
                await api_client_2.agent_api_participants.add_agent_chat_participant(
                    chat_id,
                    participant=ParticipantRequest(
                        participant_id=other_agent_id, role="member"
                    ),
                )
                add_agent_success = True
                logger.info(
                    "SUCCESS: Agent 2 added Agent '%s' to chat", other_agent_name
                )
                # Clean up: remove the added agent to keep room state clean
                try:
                    await (
                        api_client.agent_api_participants.remove_agent_chat_participant(
                            chat_id, other_agent_id
                        )
                    )
                except ApiError:
                    logger.warning("Failed to clean up added agent %s", other_agent_id)
            except ApiError as e:
                logger.info(
                    "FAILED: Agent 2 could not add Agent: %s (status %s)",
                    type(e).__name__,
                    e.status_code,
                )
                if e.status_code == 403:
                    logger.info("  -> 403 Forbidden: Members cannot add participants")
        else:
            add_agent_success = None
            logger.info("No other Agent peer available for testing")

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("SUMMARY:")
        if other_agent:
            logger.info(
                "  - Agent 2 (member) add Agent: %s",
                "SUCCESS" if add_agent_success else "FAILED (403)",
            )
        logger.info("=" * 60)

    async def test_member_agent_promoted_to_admin_can_add_participants(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
        shared_agent2_info,
        shared_user_peer,
    ):
        """Test that a member promoted to admin CAN add participants.

        Uses the shared multi-agent room. Promotes Agent 2 to admin,
        then verifies it can add a participant.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Admin agent adding participants")
        logger.info("=" * 60)

        agent2_id = shared_agent2_info.id
        agent2_name = shared_agent2_info.name
        logger.info("Agent 2 (will be Admin): %s (ID: %s)", agent2_name, agent2_id)

        chat_id = shared_multi_agent_room

        # Promote Agent 2 to admin (re-add with admin role, ignore 409 if already admin)
        try:
            await api_client.agent_api_participants.add_agent_chat_participant(
                chat_id,
                participant=ParticipantRequest(participant_id=agent2_id, role="admin"),
            )
            logger.info("Promoted Agent 2 to admin")
        except ApiError as e:
            if e.status_code == 409:
                logger.info("Agent 2 already admin (409 conflict)")
            else:
                raise

        # Verify Agent 2's role
        response = await api_client.agent_api_participants.list_agent_chat_participants(
            chat_id
        )
        participants = response.data or []
        agent2_participant = next((p for p in participants if p.id == agent2_id), None)
        assert agent2_participant is not None, "Agent 2 should be in chat"
        logger.info("Agent 2's role: %s", agent2_participant.role)

        # Find a peer not in the room to test adding
        response = await api_client.agent_api_peers.list_agent_peers()
        all_peers = response.data or []
        current_participant_ids = {p.id for p in participants}

        addable_peer = next(
            (p for p in all_peers if p.id not in current_participant_ids),
            None,
        )

        if addable_peer is None:
            pytest.skip("No addable peer available to test admin add")

        # Agent 2 (admin) adds the peer
        logger.info("\n--- Agent 2 (admin) adds %s ---", addable_peer.name)
        add_success = False
        try:
            await api_client_2.agent_api_participants.add_agent_chat_participant(
                chat_id,
                participant=ParticipantRequest(
                    participant_id=addable_peer.id, role="member"
                ),
            )
            add_success = True
            logger.info(
                "SUCCESS: Agent 2 (admin) added '%s' to chat", addable_peer.name
            )
        except ApiError as e:
            logger.info(
                "FAILED: Agent 2 (admin) could not add peer: %s (status %s)",
                e,
                e.status_code,
            )

        # Verify peer was added
        response = await api_client.agent_api_participants.list_agent_chat_participants(
            chat_id
        )
        final_participants = response.data or []
        peer_in_chat = any(p.id == addable_peer.id for p in final_participants)

        logger.info("\nFinal participants (%s):", len(final_participants))
        for p in final_participants:
            logger.info("  - %s (%s, role: %s)", p.name, p.type, p.role)

        logger.info("\n" + "=" * 60)
        if add_success and peer_in_chat:
            logger.info("SUCCESS: Admin agent CAN add participants")
        else:
            logger.info("UNEXPECTED: Admin agent could not add participants")
        logger.info("=" * 60)

        # Assert that admins CAN add participants
        assert add_success, "Admin should be able to add participants"
        assert peer_in_chat, "Peer should now be in the chat"

        # Clean up: remove the added peer to keep room state clean
        try:
            await api_client.agent_api_participants.remove_agent_chat_participant(
                chat_id, addable_peer.id
            )
        except ApiError:
            logger.warning("Failed to clean up added peer %s", addable_peer.id)
