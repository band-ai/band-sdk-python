"""WebSocket integration tests.

Tests real-time WebSocket notifications against the local platform.

Run with: uv run pytest tests/integration/test_websocket.py -v -s
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from band_rest import ChatMessageRequest, ChatRoomRequest
from band_rest.core.api_error import ApiError
from band_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
    ParticipantRequest,
)

from band.client.streaming import (
    MessageCreatedPayload,
    RoomAddedPayload,
    WebSocketClient,
)
from tests.integration.conftest import requires_api, requires_multi_agent

logger = logging.getLogger(__name__)


class TestWebSocketNotifications:
    """Test WebSocket notifications for real-time events."""

    @requires_multi_agent
    async def test_receives_message_created_event(
        self,
        api_client,
        api_client_2,
        integration_settings,
        shared_multi_agent_room,
        shared_agent1_info,
        shared_agent2_info,
    ):
        """Test that WebSocket receives message_created when @mentioned by another agent.

        Uses the shared multi-agent room. Agent 2 sends message @mentioning Agent 1,
        Agent 1 should receive the message via WebSocket.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        logger.info("\n" + "=" * 60)
        logger.info("Testing: WebSocket message_created notification (multi-agent)")
        logger.info("=" * 60)

        agent1_id = shared_agent1_info.id
        agent1_name = shared_agent1_info.name
        agent2_id = shared_agent2_info.id
        agent2_name = shared_agent2_info.name
        logger.info("Agent 1 (receiver): %s (ID: %s)", agent1_name, agent1_id)
        logger.info("Agent 2 (sender): %s (ID: %s)", agent2_name, agent2_id)

        chat_id = shared_multi_agent_room

        # Track received messages
        received_messages: list[MessageCreatedPayload] = []
        message_received = asyncio.Event()

        async def on_message_created(payload: MessageCreatedPayload):
            logger.info("  [WS] Agent 1 received message_created: %s", payload.id)
            received_messages.append(payload)
            message_received.set()

        # Agent 1 connects WebSocket and subscribes to chat room
        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key,
            agent_id=agent1_id,
        )

        async with ws:
            await ws.join_chat_room_channel(chat_id, on_message_created)
            logger.info("Agent 1 subscribed to chat_room:%s", chat_id)

            # Small delay to ensure subscription is active
            await asyncio.sleep(0.2)

            # Agent 2 sends a message @mentioning Agent 1
            message_content = f"Hello @{agent1_name}, WebSocket multi-agent test!"
            response = await api_client_2.agent_api_messages.create_agent_chat_message(
                chat_id,
                message=ChatMessageRequest(
                    content=message_content,
                    mentions=[Mention(id=agent1_id, name=agent1_name)],
                ),
            )
            sent_message_id = response.data.id
            logger.info("Agent 2 sent message via REST: %s", sent_message_id)

            # Wait for Agent 1's WebSocket to receive the message
            try:
                await asyncio.wait_for(message_received.wait(), timeout=5.0)
                logger.info("Agent 1's WebSocket received the message!")
            except asyncio.TimeoutError:
                logger.info("Timeout waiting for WebSocket message")

        # Verify Agent 1 received the message
        assert len(received_messages) > 0, (
            "Agent 1 should have received at least one message"
        )

        # Find the message Agent 2 sent
        our_message = next(
            (m for m in received_messages if m.id == sent_message_id), None
        )
        assert our_message is not None, (
            f"Agent 1 should have received message {sent_message_id}"
        )
        # Server stores mentions as @[[uuid]] format, so check for the text portion
        assert "WebSocket multi-agent test!" in our_message.content, (
            f"Message should contain expected text, got: {our_message.content}"
        )
        logger.info("Verified message content: '%s...'", our_message.content[:50])

        logger.info("\nWebSocket message_created test passed!")

    @requires_api
    async def test_receives_room_added_event(self, api_client, integration_settings):
        """Test that WebSocket receives room_added when agent is added to a new chat.

        This test MUST create a fresh room to verify the room_added WebSocket event.
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: WebSocket room_added notification")
        logger.info("=" * 60)

        # Get agent info
        response = await api_client.agent_api_identity.get_agent_me()
        agent_id = response.data.id
        agent_name = response.data.name
        logger.info("Agent: %s (ID: %s)", agent_name, agent_id)

        # Track received room events
        received_rooms: list[RoomAddedPayload] = []
        room_added = asyncio.Event()

        async def on_room_added(payload: RoomAddedPayload):
            logger.info(
                "  [WS] Received room_added: %s - %s", payload.id, payload.title
            )
            received_rooms.append(payload)
            room_added.set()

        async def on_room_removed(payload):
            logger.info("  [WS] Received room_removed: %s", payload.id)

        # Connect WebSocket and subscribe to agent rooms channel
        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key,
            agent_id=agent_id,
        )

        async with ws:
            await ws.join_agent_rooms_channel(agent_id, on_room_added, on_room_removed)
            logger.info("Subscribed to agent_rooms:%s", agent_id)

            # Small delay to ensure subscription is active
            await asyncio.sleep(0.2)

            # Create a new chat via REST API (agent is automatically owner)
            try:
                response = await api_client.agent_api_chats.create_agent_chat(
                    chat=ChatRoomRequest()
                )
            except ApiError as e:
                if e.status_code == 403:
                    pytest.skip("Chat room limit reached")
                raise
            created_chat_id = response.data.id
            logger.info("Created chat via REST: %s", created_chat_id)

            # Get a peer to add to the room so we can send a descriptive message
            peers_response = await api_client.agent_api_peers.list_agent_peers()
            if peers_response.data:
                peer = peers_response.data[0]
                await api_client.agent_api_participants.add_agent_chat_participant(
                    created_chat_id,
                    participant=ParticipantRequest(
                        participant_id=peer.id, role="member"
                    ),
                )

                # Add descriptive message (triggers auto-title)
                await api_client.agent_api_messages.create_agent_chat_message(
                    created_chat_id,
                    message=ChatMessageRequest(
                        content=f"WebSocket room_added test: @{peer.name} testing that agent receives room_added notification",
                        mentions=[Mention(id=peer.id, name=peer.name)],
                    ),
                )

            # Wait for WebSocket to receive the room_added event
            try:
                await asyncio.wait_for(room_added.wait(), timeout=5.0)
                logger.info("WebSocket received the room_added event!")
            except asyncio.TimeoutError:
                logger.info("Timeout waiting for room_added event")

        # Verify we received the room event
        assert len(received_rooms) > 0, (
            "Should have received at least one room_added event"
        )

        # Find our room in received
        our_room = next((r for r in received_rooms if r.id == created_chat_id), None)
        assert our_room is not None, (
            f"Should have received room_added for {created_chat_id}"
        )
        assert our_room.title is not None, "Room should have a title"
        logger.info("Verified room title: '%s'", our_room.title)

        logger.info("\nWebSocket room_added test passed!")
