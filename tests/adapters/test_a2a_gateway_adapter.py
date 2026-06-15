"""Unit tests for A2A Gateway context_id persistence (mock-based).

Tests the internal context mapping logic without hitting the real platform.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.a2a.gateway import A2AGatewayAdapter
from band_rest import Peer


class TestA2AGatewayContextIdFlow:
    """Unit tests for context_id persistence in A2A Gateway (mock-based)."""

    @pytest.fixture
    def gateway_adapter_with_mocks(self) -> A2AGatewayAdapter:
        """Create gateway adapter with mocked REST client for testing."""
        adapter = A2AGatewayAdapter(
            rest_url="http://localhost:4000",
            api_key="test-key",
            gateway_url="http://localhost:10000",
            port=10000,
        )

        # Mock peer
        weather_peer = Peer(
            id="uuid-weather",
            name="Weather Agent",
            type="Agent",
            handle="test/weather-agent",
            is_contact=False,
            source="registry",
        )
        adapter._peers = {"weather-agent": weather_peer}
        adapter._peers_by_uuid = {"uuid-weather": weather_peer}

        # Track room creation
        rooms_created: list[str] = []

        def track_room_creation(*args, **kwargs):
            room_id = f"room-{len(rooms_created) + 1}"
            rooms_created.append(room_id)
            response = MagicMock()
            response.data = MagicMock(id=room_id)
            return response

        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
            side_effect=track_room_creation
        )
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock()
        adapter._rest.agent_api_events.create_agent_chat_event = AsyncMock()
        adapter._rooms_created = rooms_created  # Expose for assertions

        return adapter

    @pytest.mark.asyncio
    async def test_same_context_id_twice_uses_same_room(
        self, gateway_adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Same contextId sent twice should reuse the same chat room."""
        adapter = gateway_adapter_with_mocks

        # First request with context_id="ctx-user-session"
        room_1, ctx_1 = await adapter._get_or_create_room(
            "ctx-user-session", "uuid-weather"
        )

        # Second request with SAME context_id
        room_2, ctx_2 = await adapter._get_or_create_room(
            "ctx-user-session", "uuid-weather"
        )

        # Assertions
        assert room_1 == room_2, f"Expected same room, got {room_1} vs {room_2}"
        assert ctx_1 == ctx_2 == "ctx-user-session"
        assert len(adapter._rooms_created) == 1, "Should only create 1 room"
        assert adapter._rest.agent_api_chats.create_agent_chat.call_count == 1

    @pytest.mark.asyncio
    async def test_different_context_ids_create_different_rooms(
        self, gateway_adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Different contextIds should create separate chat rooms."""
        adapter = gateway_adapter_with_mocks

        # First context
        room_a, ctx_a = await adapter._get_or_create_room(
            "ctx-session-a", "uuid-weather"
        )

        # Second context (different)
        room_b, ctx_b = await adapter._get_or_create_room(
            "ctx-session-b", "uuid-weather"
        )

        # Assertions
        assert room_a != room_b, f"Expected different rooms, got same: {room_a}"
        assert ctx_a == "ctx-session-a"
        assert ctx_b == "ctx-session-b"
        assert len(adapter._rooms_created) == 2, "Should create 2 separate rooms"

    @pytest.mark.asyncio
    async def test_same_context_different_peers_same_room_adds_peer(
        self, gateway_adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Same contextId with different peers should use same room, add peer."""
        adapter = gateway_adapter_with_mocks

        # Add second peer
        data_peer = Peer(
            id="uuid-data",
            name="Data Agent",
            type="Agent",
            handle="test/data-agent",
            is_contact=False,
            source="registry",
        )
        adapter._peers["data-agent"] = data_peer
        adapter._peers_by_uuid["uuid-data"] = data_peer

        # First peer
        room_1, _ = await adapter._get_or_create_room("ctx-multi", "uuid-weather")

        # Second peer, same context
        room_2, _ = await adapter._get_or_create_room("ctx-multi", "uuid-data")

        # Assertions
        assert room_1 == room_2, "Same context should use same room"
        assert len(adapter._rooms_created) == 1, "Should only create 1 room"
        assert "uuid-weather" in adapter._room_participants[room_1]
        assert "uuid-data" in adapter._room_participants[room_1]
        assert (
            adapter._rest.agent_api_participants.add_agent_chat_participant.call_count
            == 2
        )
