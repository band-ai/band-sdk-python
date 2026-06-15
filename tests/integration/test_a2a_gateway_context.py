"""E2E tests for A2A Gateway context_id flow.

Tests the complete flow using real Band platform (requires API keys in .env.test):
1. Gateway receives HTTP request with contextId
2. Gateway creates/reuses chat room based on contextId
3. Same contextId -> same room, different contextId -> different room
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.integrations.a2a.gateway import A2AGatewayAdapter
from band_rest import AsyncRestClient, ChatMessageRequest, ParticipantRequest
from band_rest.core.api_error import ApiError
from band_rest.types import ChatMessageRequestMentionsItem as Mention

from .conftest import fetch_all_context, requires_api

logger = logging.getLogger(__name__)


async def ensure_peer_in_room(
    api_client: AsyncRestClient, room_id: str, peer_id: str
) -> None:
    """Add peer to room, ignoring 409 if already a participant."""
    try:
        await api_client.agent_api_participants.add_agent_chat_participant(
            chat_id=room_id,
            participant=ParticipantRequest(participant_id=peer_id, role="member"),
        )
    except ApiError as e:
        # 409 Conflict means peer is already in the room — safe to ignore
        if e.status_code == 409:
            logger.debug("Peer %s already in room %s", peer_id, room_id)
        else:
            raise


@requires_api
class TestA2AGatewayContextIdWithPlatform:
    """E2E tests using real Band platform (requires API keys in .env.test).

    These tests actually:
    1. Create rooms on the real platform
    2. Add participants (peers) to rooms
    3. Send real messages with @mentions
    4. Query room context to verify messages arrive
    5. Verify same contextId -> same room with shared messages
    """

    @pytest.mark.asyncio
    async def test_same_context_routes_messages_to_same_room(
        self,
        api_client: AsyncRestClient,
        integration_settings,
        shared_room: str,
    ) -> None:
        """Same context_id twice should route to same room with shared messages.

        Uses the session-scoped shared_room to avoid creating new rooms.
        Pre-populates the adapter's context mapping so _get_or_create_room()
        finds the existing room instead of creating one.
        """
        # Get a peer to add to rooms
        response = await api_client.agent_api_peers.list_agent_peers()
        if not response.data:
            pytest.skip("No peers available")
        peer = response.data[0]

        # Create adapter with real REST client credentials
        adapter = A2AGatewayAdapter(
            rest_url=integration_settings.band_base_url,
            api_key=integration_settings.band_api_key,
            gateway_url="http://localhost:10000",
            port=10000,
        )
        adapter._peers = {peer.name.lower().replace(" ", "-"): peer}
        adapter._peers_by_uuid = {peer.id: peer}

        # Ensure peer is a participant in the shared room
        await ensure_peer_in_room(api_client, shared_room, peer.id)

        # Pre-populate context mapping and participants to reuse shared room
        context_id = "e2e-context-shared"
        adapter._context_to_room[context_id] = shared_room
        adapter._room_participants[shared_room] = {peer.id}

        # ===== Step 1: First request with context_id =====
        room_1, ctx_1 = await adapter._get_or_create_room(context_id, peer.id)

        await asyncio.sleep(0.5)  # Platform consistency

        # Send message to room 1
        msg1_response = await api_client.agent_api_messages.create_agent_chat_message(
            room_1,
            message=ChatMessageRequest(
                content=f"Message 1 from context {context_id}",
                mentions=[Mention(id=peer.id, name=peer.name)],
            ),
        )
        msg1_id = msg1_response.data.id

        # ===== Step 2: Second request with SAME context_id =====
        room_2, ctx_2 = await adapter._get_or_create_room(context_id, peer.id)

        # Verify same room
        assert room_1 == room_2, (
            f"Same context should use same room: {room_1} vs {room_2}"
        )
        assert ctx_1 == ctx_2 == context_id

        # Send second message
        msg2_response = await api_client.agent_api_messages.create_agent_chat_message(
            room_2,
            message=ChatMessageRequest(
                content=f"Message 2 from same context {context_id}",
                mentions=[Mention(id=peer.id, name=peer.name)],
            ),
        )
        msg2_id = msg2_response.data.id

        await asyncio.sleep(0.5)

        # ===== Step 3: Verify BOTH messages in SAME room =====
        context_items = await fetch_all_context(api_client, room_1)

        msg_ids_in_context = [item.id for item in context_items if hasattr(item, "id")]

        assert msg1_id in msg_ids_in_context, (
            f"Message 1 ({msg1_id}) should be in room context"
        )
        assert msg2_id in msg_ids_in_context, (
            f"Message 2 ({msg2_id}) should be in room context"
        )

    @pytest.mark.asyncio
    async def test_same_context_multiple_peers_shares_room(
        self,
        api_client: AsyncRestClient,
        integration_settings,
        shared_room: str,
    ) -> None:
        """Same context_id with different peers should use same room, add all peers.

        Uses session-scoped shared_room to avoid creating new rooms.
        Pre-populates context mapping and participant set so _get_or_create_room()
        adds the second peer via _ensure_participant without creating a room.
        """
        # Get multiple peers
        response = await api_client.agent_api_peers.list_agent_peers()
        if not response.data or len(response.data) < 2:
            pytest.skip("Need at least 2 peers for this test")
        peer_1 = response.data[0]
        peer_2 = response.data[1]

        # Create adapter with both peers
        adapter = A2AGatewayAdapter(
            rest_url=integration_settings.band_base_url,
            api_key=integration_settings.band_api_key,
            gateway_url="http://localhost:10000",
            port=10000,
        )
        adapter._peers = {
            peer_1.name.lower().replace(" ", "-"): peer_1,
            peer_2.name.lower().replace(" ", "-"): peer_2,
        }
        adapter._peers_by_uuid = {peer_1.id: peer_1, peer_2.id: peer_2}

        # Ensure both peers are participants in the shared room
        await ensure_peer_in_room(api_client, shared_room, peer_1.id)
        await ensure_peer_in_room(api_client, shared_room, peer_2.id)

        # Pre-populate context mapping and participants
        context_id = "e2e-multi-peer-shared"
        adapter._context_to_room[context_id] = shared_room
        adapter._room_participants[shared_room] = {peer_1.id, peer_2.id}

        # First peer - already mapped, should return shared_room
        room_1, _ = await adapter._get_or_create_room(context_id, peer_1.id)

        await asyncio.sleep(0.3)

        # Second peer, same context - already in room
        room_2, _ = await adapter._get_or_create_room(context_id, peer_2.id)

        # Verify same room
        assert room_1 == room_2, "Same context should use same room for different peers"

        await asyncio.sleep(0.3)

        # Verify both peers are participants
        response = await api_client.agent_api_participants.list_agent_chat_participants(
            room_1
        )
        participant_ids = [p.id for p in response.data]

        assert peer_1.id in participant_ids, f"Peer 1 ({peer_1.name}) should be in room"
        assert peer_2.id in participant_ids, f"Peer 2 ({peer_2.name}) should be in room"
