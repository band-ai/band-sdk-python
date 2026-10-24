from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools


class TestMentionResolution:
    """Test mention resolution logic."""

    def test_resolve_string_mentions(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should resolve string mentions to dicts with id and handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["User One", "User Two"])

        assert len(resolved) == 2
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}
        assert resolved[1] == {"id": "user-2", "handle": "@user-two"}

    def test_resolve_dict_mentions_with_id(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should pass through dict mentions with ID and handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions([{"id": "custom-id", "handle": "@custom"}])

        assert resolved[0] == {"id": "custom-id", "handle": "@custom"}

    def test_resolve_dict_mentions_without_id(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should resolve dict mentions without ID by name lookup."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions([{"name": "User One"}])

        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}

    def test_resolve_unknown_raises(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should raise for unknown mention."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ValueError, match="Unknown participant"):
            tools._resolve_mentions(["Unknown Person"])
