from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.tools import AgentTools


class TestAgentToolsAddParticipant:
    """Test add_participant tool."""

    async def test_add_participant_by_name(self, mock_rest_client: MagicMock) -> None:
        """add_participant() should match by name and add via REST."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("Agent Two", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["role"] == "member"
        assert result["status"] == "added"
        mock_rest_client.agent_api_participants.add_agent_chat_participant.assert_called_once()

    async def test_add_participant_by_handle(self, mock_rest_client: MagicMock) -> None:
        """add_participant() should match by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("agent-two", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        assert result["status"] == "added"

    async def test_add_participant_by_id(self, mock_rest_client: MagicMock) -> None:
        """add_participant() should match by ID."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("agent-2", role="member")

        assert result["id"] == "agent-2"
        assert result["name"] == "Agent Two"
        # Plain text, not the enum member: adapters render the result with str().
        assert "'status': 'added'" in str(result)

    async def test_add_participant_already_in_room_by_handle(
        self, mock_rest_client: MagicMock
    ) -> None:
        """add_participant() should detect already-in-room by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.add_participant("user-one", role="member")

        assert result["id"] == "user-1"
        assert "'status': 'already_in_room'" in str(result)
        mock_rest_client.agent_api_participants.add_agent_chat_participant.assert_not_called()

    async def test_add_participant_ambiguous_name_resolved_by_handle(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Two peers with the same display name — handle disambiguates."""
        peer_a = MagicMock()
        peer_a.id = "agent-a"
        peer_a.name = "Weather Agent"
        peer_a.type = "Agent"
        peer_a.handle = "@alice/weather"
        peer_a.description = "Alice's weather agent"

        peer_b = MagicMock()
        peer_b.id = "agent-b"
        peer_b.name = "Weather Agent"
        peer_b.type = "Agent"
        peer_b.handle = "@bob/weather"
        peer_b.description = "Bob's weather agent"

        peers_response = MagicMock()
        peers_response.data = [peer_a, peer_b]
        peers_response.metadata = MagicMock()
        peers_response.metadata.page = 1
        peers_response.metadata.page_size = 100
        peers_response.metadata.total_count = 2
        peers_response.metadata.total_pages = 1
        mock_rest_client.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=peers_response
        )

        tools = AgentTools("room-123", mock_rest_client)

        # Using handle should pick the correct one
        result = await tools.add_participant("@bob/weather", role="member")

        assert result["id"] == "agent-b"
        assert result["name"] == "Weather Agent"
        assert result["status"] == "added"

    async def test_add_participant_not_found_raises(
        self, mock_rest_client: MagicMock
    ) -> None:
        """add_participant() should raise if peer not found."""
        # Return empty peers
        mock_rest_client.agent_api_peers.list_agent_peers.return_value = MagicMock(
            data=[], metadata=MagicMock(total_pages=1)
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="Participant 'Unknown' not found"):
            await tools.add_participant("Unknown")
