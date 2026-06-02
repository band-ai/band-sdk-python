"""Tests for GatewayHistoryConverter."""

from __future__ import annotations

import pytest

from band.converters.a2a_gateway import GatewayHistoryConverter
from band.integrations.a2a.gateway.types import GatewaySessionState


class TestGatewayHistoryConverter:
    """Tests for GatewayHistoryConverter."""

    @pytest.fixture
    def converter(self) -> GatewayHistoryConverter:
        """Create a converter instance."""
        return GatewayHistoryConverter()

    def test_convert_empty_history(self, converter: GatewayHistoryConverter) -> None:
        """Empty history returns empty state."""
        result = converter.convert([])
        assert result.context_to_room == {}
        assert result.room_participants == {}
        assert isinstance(result, GatewaySessionState)

    def test_extract_context_to_room_from_metadata(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should extract context_id → room_id from metadata."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-123",
                    "gateway_room_id": "room-456",
                },
            }
        ]
        result = converter.convert(raw)
        assert result.context_to_room == {"ctx-123": "room-456"}

    def test_extract_context_uses_room_id_fallback(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should fall back to msg room_id if gateway_room_id not in metadata."""
        raw = [
            {
                "message_type": "task",
                "room_id": "room-789",
                "metadata": {
                    "gateway_context_id": "ctx-123",
                },
            }
        ]
        result = converter.convert(raw)
        assert result.context_to_room == {"ctx-123": "room-789"}

    def test_extract_room_participants(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should track participants from agent message senders."""
        raw = [
            {
                "message_type": "text",
                "sender_id": "weather-agent",
                "sender_type": "agent",
                "room_id": "room-1",
            },
            {
                "message_type": "text",
                "sender_id": "servicenow-agent",
                "sender_type": "agent",
                "room_id": "room-1",
            },
        ]
        result = converter.convert(raw)
        assert result.room_participants == {
            "room-1": {"weather-agent", "servicenow-agent"}
        }

    def test_multiple_events_aggregates_all_contexts(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should aggregate all context mappings from multiple events."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-1",
                    "gateway_room_id": "room-1",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-2",
                    "gateway_room_id": "room-2",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-3",
                    "gateway_room_id": "room-3",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.context_to_room == {
            "ctx-1": "room-1",
            "ctx-2": "room-2",
            "ctx-3": "room-3",
        }

    def test_multiple_events_aggregates_all_participants(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should aggregate participants across multiple messages."""
        raw = [
            {
                "message_type": "text",
                "sender_id": "peer-1",
                "sender_type": "agent",
                "room_id": "room-1",
            },
            {
                "message_type": "text",
                "sender_id": "peer-2",
                "sender_type": "agent",
                "room_id": "room-1",
            },
            {
                "message_type": "text",
                "sender_id": "peer-3",
                "sender_type": "agent",
                "room_id": "room-2",
            },
        ]
        result = converter.convert(raw)
        assert result.room_participants == {
            "room-1": {"peer-1", "peer-2"},
            "room-2": {"peer-3"},
        }

    def test_handles_missing_metadata(self, converter: GatewayHistoryConverter) -> None:
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
        assert result.context_to_room == {}
        assert result.room_participants == {}

    def test_ignores_user_senders(self, converter: GatewayHistoryConverter) -> None:
        """Should only track agent senders, not user senders."""
        raw = [
            {
                "message_type": "text",
                "sender_id": "user-123",
                "sender_type": "user",
                "room_id": "room-1",
            },
            {
                "message_type": "text",
                "sender_id": "agent-456",
                "sender_type": "agent",
                "room_id": "room-1",
            },
        ]
        result = converter.convert(raw)
        assert result.room_participants == {"room-1": {"agent-456"}}

    def test_ignores_messages_without_room_id(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should skip participant tracking for messages without room_id."""
        raw = [
            {
                "message_type": "text",
                "sender_id": "agent-1",
                "sender_type": "agent",
                # No room_id
            },
        ]
        result = converter.convert(raw)
        assert result.room_participants == {}

    def test_ignores_messages_without_sender_id(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should skip participant tracking for messages without sender_id."""
        raw = [
            {
                "message_type": "text",
                "sender_type": "agent",
                "room_id": "room-1",
                # No sender_id
            },
        ]
        result = converter.convert(raw)
        assert result.room_participants == {}

    def test_later_context_overwrites_earlier(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Later mapping for same context_id should overwrite earlier."""
        raw = [
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-1",
                    "gateway_room_id": "room-old",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "gateway_context_id": "ctx-1",
                    "gateway_room_id": "room-new",
                },
            },
        ]
        result = converter.convert(raw)
        assert result.context_to_room == {"ctx-1": "room-new"}

    def test_handles_tool_calls_in_history(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should handle tool_call and tool_result messages gracefully."""
        raw = [
            {
                "message_type": "text",
                "sender_id": "weather-agent",
                "sender_type": "agent",
                "room_id": "room-1",
                "content": "Let me check the weather.",
            },
            {
                "message_type": "tool_call",
                "sender_id": "weather-agent",
                "sender_type": "agent",
                "room_id": "room-1",
                "content": "get_weather",
                "metadata": {
                    "tool_name": "get_weather",
                    "arguments": {"location": "NYC"},
                },
            },
            {
                "message_type": "tool_result",
                "sender_id": "weather-agent",
                "sender_type": "agent",
                "room_id": "room-1",
                "content": "72°F, sunny",
                "metadata": {
                    "tool_name": "get_weather",
                    "status": "success",
                },
            },
            {
                "message_type": "text",
                "sender_id": "weather-agent",
                "sender_type": "agent",
                "room_id": "room-1",
                "content": "The weather in NYC is 72°F and sunny.",
            },
        ]
        result = converter.convert(raw)
        # Tool calls should not break participant tracking
        assert result.room_participants == {"room-1": {"weather-agent"}}
        # No context mapping since no gateway metadata
        assert result.context_to_room == {}

    def test_tool_calls_with_gateway_metadata(
        self, converter: GatewayHistoryConverter
    ) -> None:
        """Should extract context from tool calls that have gateway metadata."""
        raw = [
            {
                "message_type": "tool_call",
                "sender_id": "orchestrator",
                "sender_type": "agent",
                "room_id": "room-1",
                "metadata": {
                    "gateway_context_id": "ctx-from-tool",
                    "gateway_room_id": "room-1",
                    "tool_name": "band_send_message",
                },
            },
            {
                "message_type": "tool_result",
                "sender_id": "orchestrator",
                "sender_type": "agent",
                "room_id": "room-1",
                "metadata": {
                    "tool_name": "band_send_message",
                    "status": "success",
                },
            },
        ]
        result = converter.convert(raw)
        # Should extract context from tool_call with gateway metadata
        assert result.context_to_room == {"ctx-from-tool": "room-1"}
        assert result.room_participants == {"room-1": {"orchestrator"}}
