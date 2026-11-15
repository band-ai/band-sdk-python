from __future__ import annotations

from unittest.mock import MagicMock

from band.runtime.tools import AgentTools


class TestAgentToolsConstruction:
    """Test AgentTools initialization."""

    def test_init_stores_room_id(self, mock_rest_client: MagicMock) -> None:
        """Should store room_id."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.room_id == "room-123"

    def test_init_stores_rest_client(self, mock_rest_client: MagicMock) -> None:
        """Should store REST client."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools.rest is mock_rest_client

    def test_init_empty_participants_by_default(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should have empty participants by default."""
        tools = AgentTools("room-123", mock_rest_client)

        assert tools._participants == []

    def test_init_with_participants(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should accept participants."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        assert tools._participants == participants
