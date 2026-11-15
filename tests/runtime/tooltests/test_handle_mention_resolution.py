from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.tools import (
    AgentTools,
    append_mention_handles_hint,
    available_mention_handles,
)


class TestHandleMentionResolution:
    """Test handle-based mention resolution."""

    def test_available_mention_handles_excludes_self_and_missing_handles(self) -> None:
        """Available handle hints should include only mentionable room handles."""
        participants = [
            {"id": "user-1", "name": "User One", "handle": "@user-one"},
            {"id": "self", "name": "Self", "handle": "@self"},
            {"id": "user-3", "name": "No Handle", "handle": None},
        ]

        assert available_mention_handles(participants, agent_id="self") == ["@user-one"]

    def test_append_mention_handles_hint_is_idempotent(self) -> None:
        """An error already carrying the hint is returned unchanged, so the same
        error can pass through multiple adapter enrichers without doubling."""
        enriched = append_mention_handles_hint(
            "At least one mention is required", ["@alice"]
        )
        assert enriched.count("Available handles:") == 1

        twice = append_mention_handles_hint(enriched, ["@alice"])
        assert twice == enriched

    def test_append_mention_handles_hint_no_handles_is_noop(self) -> None:
        """With no mentionable handles there is nothing to suggest."""
        error = "At least one mention is required"
        assert append_mention_handles_hint(error, []) == error

    def test_resolve_by_handle(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should resolve mentions by handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["@user-one"])

        assert len(resolved) == 1
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}

    def test_resolve_handle_takes_priority(self, mock_rest_client: MagicMock) -> None:
        """Should try handle lookup before name lookup."""
        # Participant with handle different from name
        participants = [
            {
                "id": "agent-1",
                "name": "Weather Agent",
                "type": "Agent",
                "handle": "@john/weather",
            },
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        # Resolve by handle
        resolved = tools._resolve_mentions(["@john/weather"])
        assert resolved[0] == {"id": "agent-1", "handle": "@john/weather"}

        # Resolve by name still works
        resolved = tools._resolve_mentions(["Weather Agent"])
        assert resolved[0] == {"id": "agent-1", "handle": "@john/weather"}

    def test_resolve_mixed_handles_and_names(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should resolve a mix of handles and names."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["@user-one", "User Two"])

        assert len(resolved) == 2
        assert resolved[0] == {"id": "user-1", "handle": "@user-one"}
        assert resolved[1] == {"id": "user-2", "handle": "@user-two"}

    def test_resolve_unknown_handle_raises(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should raise for unknown handle."""
        tools = AgentTools(
            "room-123", mock_rest_client, participants, agent_id="user-2"
        )

        # @ prefix is stripped during normalization
        with pytest.raises(ValueError, match="Unknown participant 'unknown'") as exc:
            tools._resolve_mentions(["@unknown"])

        message = str(exc.value)
        assert "@user-one" in message
        assert "@user-two" not in message

    def test_resolve_participant_without_handle(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should resolve by name when participant has no handle."""
        participants = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": None},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        resolved = tools._resolve_mentions(["User One"])

        assert resolved[0] == {"id": "user-1", "handle": None}
