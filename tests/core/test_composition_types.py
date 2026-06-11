"""Tests for composition layer types."""

from datetime import datetime, timezone

import pytest

from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing import FakeAgentTools


class TestPlatformMessage:
    """Tests for PlatformMessage dataclass."""

    def test_format_for_llm_with_sender_name(self):
        """Should format with sender name when available."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello world",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        result = msg.format_for_llm()

        assert result == "[Alice]: Hello world"

    def test_format_for_llm_falls_back_to_sender_type(self):
        """Should fall back to sender_type when sender_name is None."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello",
            sender_id="user-1",
            sender_type="User",
            sender_name=None,
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        result = msg.format_for_llm()

        assert result == "[User]: Hello"

    def test_format_for_llm_falls_back_to_unknown(self):
        """Should fall back to 'Unknown' when both are missing."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello",
            sender_id="user-1",
            sender_type="",
            sender_name=None,
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        result = msg.format_for_llm()

        assert result == "[Unknown]: Hello"

    def test_is_frozen(self):
        """PlatformMessage should be immutable."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            msg.content = "Modified"


class TestHistoryProvider:
    """Tests for HistoryProvider lazy conversion."""

    def test_stores_raw_history(self):
        """Should store raw history list."""
        raw = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        provider = HistoryProvider(raw=raw)

        assert provider.raw == raw
        assert len(provider) == 2

    def test_bool_true_when_not_empty(self):
        """Should be truthy when history exists."""
        provider = HistoryProvider(raw=[{"role": "user", "content": "Hi"}])

        assert bool(provider) is True

    def test_bool_false_when_empty(self):
        """Should be falsy when empty."""
        provider = HistoryProvider(raw=[])

        assert bool(provider) is False

    def test_convert_with_converter(self):
        """Should convert using provided converter."""
        raw = [
            {"role": "user", "content": "Hello", "sender_name": "Alice"},
            {"role": "assistant", "content": "Hi!"},
        ]
        provider = HistoryProvider(raw=raw)

        # Simple test converter
        class UppercaseConverter:
            def convert(self, raw: list) -> list[str]:
                return [h["content"].upper() for h in raw]

        result = provider.convert(UppercaseConverter())

        assert result == ["HELLO", "HI!"]

    def test_convert_preserves_type(self):
        """Converter output type should be preserved."""
        raw = [{"role": "user", "content": "Test"}]
        provider = HistoryProvider(raw=raw)

        class DictConverter:
            def convert(self, raw: list) -> dict:
                return {"count": len(raw), "first": raw[0]["content"]}

        result = provider.convert(DictConverter())

        assert result == {"count": 1, "first": "Test"}


class TestAgentInput:
    """Tests for AgentInput dataclass."""

    def test_construction(self):
        """Should construct with all required fields."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )
        tools = FakeAgentTools()
        history = HistoryProvider(raw=[])

        inp = AgentInput(
            msg=msg,
            tools=tools,
            history=history,
            participants_msg="Alice joined",
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert inp.msg == msg
        assert inp.tools == tools
        assert inp.history == history
        assert inp.participants_msg == "Alice joined"
        assert inp.is_session_bootstrap is True
        assert inp.room_id == "room-1"

    def test_participants_msg_can_be_none(self):
        """participants_msg should accept None."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello",
            sender_id="user-1",
            sender_type="User",
            sender_name=None,
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        inp = AgentInput(
            msg=msg,
            tools=FakeAgentTools(),
            history=HistoryProvider(raw=[]),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert inp.participants_msg is None

    def test_is_frozen(self):
        """AgentInput should be immutable."""
        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="Hello",
            sender_id="user-1",
            sender_type="User",
            sender_name=None,
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        inp = AgentInput(
            msg=msg,
            tools=FakeAgentTools(),
            history=HistoryProvider(raw=[]),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            inp.room_id = "modified"
