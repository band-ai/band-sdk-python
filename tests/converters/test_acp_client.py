"""Tests for ACPClientHistoryConverter."""

from __future__ import annotations

import pytest

from band.converters.acp_client import ACPClientHistoryConverter
from band.integrations.acp.client_types import ACPClientSessionState


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
        assert isinstance(result, ACPClientSessionState)

    def test_extract_room_to_session_from_metadata(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should extract room_id -> session_id from metadata."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_client_session_id": "session-123",
                    "acp_client_room_id": "room-456",
                },
            }
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {"room-456": "session-123"}

    def test_multiple_mappings_extracted(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Should extract all room -> session mappings."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_client_session_id": "session-1",
                    "acp_client_room_id": "room-1",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "acp_client_session_id": "session-2",
                    "acp_client_room_id": "room-2",
                },
            },
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
            {
                "message_type": "task",
                "metadata": {
                    "acp_client_session_id": "session-123",
                    # Missing acp_client_room_id
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    # Missing acp_client_session_id
                    "acp_client_room_id": "room-456",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {}

    def test_later_mapping_overwrites_earlier(
        self, converter: ACPClientHistoryConverter
    ) -> None:
        """Later mapping for same room should overwrite earlier."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "acp_client_session_id": "session-old",
                    "acp_client_room_id": "room-1",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "acp_client_session_id": "session-new",
                    "acp_client_room_id": "room-1",
                },
            },
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
        raw = [
            {
                "message_type": "text",
                "content": "Hello world",
            },
        ]
        result = converter.convert(raw)
        assert result.room_to_session == {}
