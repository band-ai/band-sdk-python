"""Tests for ACPClientHistoryConverter."""

from __future__ import annotations

from typing import Any

import pytest

from band.converters.acp_client import ACPClientHistoryConverter
from band.integrations.acp.client_types import ACPClientSessionState


def text(sender: str, content: str) -> dict[str, Any]:
    """A room text message as platform history delivers it."""
    return {"message_type": "text", "sender_name": sender, "content": content}


def narration(message_type: str, content: str) -> dict[str, Any]:
    """An adapter narration event (thought/tool_call/tool_result)."""
    return {
        "message_type": message_type,
        "sender_name": "ACP Agent",
        "content": content,
    }


def session_task(
    session_id: str | None = None, room_id: str | None = None
) -> dict[str, Any]:
    """The adapter's session-bookkeeping task event; omit args for bare metadata."""
    metadata: dict[str, Any] = {}
    if session_id is not None:
        metadata["acp_client_session_id"] = session_id
    if room_id is not None:
        metadata["acp_client_room_id"] = room_id
    return {
        "message_type": "task",
        "content": "ACP session established",
        "metadata": metadata,
    }


class TestACPClientHistoryConverter:
    """Tests for ACPClientHistoryConverter."""

    @pytest.fixture
    def converter(self) -> ACPClientHistoryConverter:
        """Create a converter instance."""
        return ACPClientHistoryConverter()

    def test_convert_empty_history(self, converter: ACPClientHistoryConverter) -> None:
        """Empty history returns empty state."""
        result = converter.convert([])
        assert result.room_to_session == {}
        assert result.replay_messages == []
        assert isinstance(result, ACPClientSessionState)

    def test_replay_messages_render_room_text_history(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Text messages survive as replay lines, so a fresh remote session can
        be re-seeded with the room transcript when session/load fails."""
        raw = [
            text("Alice", "What is the plan?"),
            text("ACP Agent", "Working on it."),
        ]
        result = converter.convert(raw)
        assert result.replay_messages == [
            "[Alice]: What is the plan?",
            "[ACP Agent]: Working on it.",
        ]

    def test_replay_skips_adapter_narration_and_bookkeeping(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """The adapter narrates every turn into the room (thoughts, tool calls,
        session task events); none of that may be replayed back to the remote
        agent as conversation, while the same events still feed session resume.
        Whitespace-only text is noise, not a line."""
        raw = [
            text("Alice", "hello"),
            text("Alice", "   "),
            narration("thought", "thinking..."),
            narration("tool_call", '{"name": "grep"}'),
            narration("tool_result", '{"output": "3 hits"}'),
            session_task("session-123", "room-456"),
        ]
        result = converter.convert(raw)
        assert result.replay_messages == ["[Alice]: hello"]
        assert result.room_to_session == {"room-456": "session-123"}

    def test_extract_room_to_session_from_metadata(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should extract room_id -> session_id from metadata."""
        result = converter.convert([session_task("session-123", "room-456")])
        assert result.room_to_session == {"room-456": "session-123"}

    def test_multiple_mappings_extracted(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should extract all room -> session mappings."""
        raw = [
            session_task("session-1", "room-1"),
            session_task("session-2", "room-2"),
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {
            "room-1": "session-1",
            "room-2": "session-2",
        }

    def test_handles_missing_metadata(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should handle messages without metadata gracefully."""
        raw = [
            text("Alice", "Hello"),
            session_task(),  # bookkeeping event with empty metadata
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {}

    def test_handles_none_metadata(self, converter: ACPClientHistoryConverter) -> None:
        """Should handle messages with None metadata gracefully."""
        raw = [
            {
                "message_type": "task",
                "metadata": None,
            },
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {}

    def test_requires_both_keys(self, converter: ACPClientHistoryConverter) -> None:
        """Should require both session_id and room_id."""
        raw = [
            session_task(session_id="session-123"),  # missing room id
            session_task(room_id="room-456"),  # missing session id
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {}

    def test_later_mapping_overwrites_earlier(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Later mapping for same room should overwrite earlier."""
        raw = [
            session_task("session-old", "room-1"),
            session_task("session-new", "room-1"),
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {"room-1": "session-new"}

    def test_ignores_non_acp_client_metadata(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should ignore metadata that doesn't contain ACP client keys."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-123",  # Server key, not client
                    "acp_room_id": "room-456",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {}

    def test_handles_messages_without_metadata_key(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should handle messages that have no metadata key at all."""
        result = converter.convert([text("Alice", "Hello world")])
        assert result.room_to_session == {}
