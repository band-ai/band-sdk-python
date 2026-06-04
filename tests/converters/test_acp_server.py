"""Tests for ACPServerHistoryConverter."""

from __future__ import annotations

import pytest

from band.converters.acp_server import ACPServerHistoryConverter
from band.integrations.acp.types import ACPSessionState


class TestACPServerHistoryConverter:
    """Tests for ACPServerHistoryConverter."""

    @pytest.fixture
    def converter(self) -> ACPServerHistoryConverter:
        """Create a converter instance."""
        return ACPServerHistoryConverter()

    def test_convert_empty_history(self, converter: ACPServerHistoryConverter) -> None:
        """Empty history returns empty state."""
        result = converter.convert([])
        assert result.session_to_room == {}
        assert result.session_cwd == {}
        assert result.session_mcp_servers == {}
        assert isinstance(result, ACPSessionState)

    def test_extract_session_to_room_from_metadata(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should extract session_id -> room_id from metadata."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-123",
                    "acp_room_id": "room-456",
                },
            }
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {"session-123": "room-456"}

    def test_extracts_cwd_and_mcp_servers(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should restore stored editor session context."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-123",
                    "acp_room_id": "room-456",
                    "acp_cwd": "/workspace/project",
                    "acp_mcp_servers": [
                        {
                            "type": "stdio",
                            "name": "filesystem",
                            "command": "mcp-filesystem",
                        }
                    ],
                },
            }
        ]
        result = converter.convert(raw)
        assert result.session_cwd == {"session-123": "/workspace/project"}
        assert result.session_mcp_servers == {
            "session-123": [
                {
                    "type": "stdio",
                    "name": "filesystem",
                    "command": "mcp-filesystem",
                }
            ]
        }

    def test_extract_session_uses_room_id_fallback(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should fall back to msg room_id if acp_room_id not in metadata."""
        raw = [
            {
                "message_type": "task",
                "room_id": "room-789",
                "metadata": {
                    "acp_session_id": "session-123",
                },
            }
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {"session-123": "room-789"}

    def test_multiple_sessions_extracted(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should extract all session mappings from multiple events."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-1",
                    "acp_room_id": "room-1",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-2",
                    "acp_room_id": "room-2",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-3",
                    "acp_room_id": "room-3",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {
            "session-1": "room-1",
            "session-2": "room-2",
            "session-3": "room-3",
        }

    def test_handles_missing_metadata(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should handle messages without metadata gracefully."""
        raw = [
            {
                "message_type": "text",
                "content": "Hello",
            },
            {
                "message_type": "task",
                "metadata": {},
            },
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {}

    def test_handles_none_metadata(self, converter: ACPServerHistoryConverter) -> None:
        """Should handle messages with None metadata gracefully."""
        raw = [
            {
                "message_type": "task",
                "metadata": None,
            },
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {}

    def test_later_session_overwrites_earlier(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Later mapping for same session_id should overwrite earlier."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-1",
                    "acp_room_id": "room-old",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "acp_session_id": "session-1",
                    "acp_room_id": "room-new",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {"session-1": "room-new"}

    def test_ignores_non_acp_metadata(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should ignore metadata that doesn't contain ACP keys."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-123",
                    "gateway_room_id": "room-456",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "a2a_context_id": "ctx-789",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {}

    def test_handles_messages_without_metadata_key(
        self, converter: ACPServerHistoryConverter
    ) -> None:
        """Should handle messages that have no metadata key at all."""
        raw = [
            {
                "message_type": "text",
                "content": "Hello world",
            },
        ]
        result = converter.convert(raw)
        assert result.session_to_room == {}
