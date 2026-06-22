"""Tests for SimpleAdapter base class."""

from datetime import datetime, timezone
from typing import Any

from band.core.protocols import FrameworkAdapter, HistoryConverter
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing import FakeAgentTools


class StringHistoryConverter(HistoryConverter[str]):
    """Test converter that joins history into a string."""

    def convert(self, raw: list[dict[str, Any]]) -> str:
        return " | ".join(h.get("content", "") for h in raw)


class ListHistoryConverter(HistoryConverter[list[str]]):
    """Test converter that extracts content to list."""

    def convert(self, raw: list[dict[str, Any]]) -> list[str]:
        return [h.get("content", "") for h in raw]


class RecordingAdapter(SimpleAdapter[str]):
    """Test adapter that records calls for verification."""

    def __init__(self, *, history_converter: HistoryConverter[str] | None = None):
        super().__init__(history_converter=history_converter)
        self.calls: list[dict] = []
        self.cleanup_calls: list[str] = []

    async def on_message(
        self,
        msg: PlatformMessage,
        tools,
        history,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.calls.append(
            {
                "msg": msg,
                "tools": tools,
                "history": history,
                "participants_msg": participants_msg,
                "contacts_msg": contacts_msg,
                "is_session_bootstrap": is_session_bootstrap,
                "room_id": room_id,
            }
        )

    async def on_cleanup(self, room_id: str) -> None:
        self.cleanup_calls.append(room_id)


def make_platform_message(content: str = "Hello") -> PlatformMessage:
    """Create a test PlatformMessage."""
    return PlatformMessage(
        id="msg-1",
        room_id="room-1",
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def make_agent_input(
    content: str = "Hello",
    raw_history: list[dict[str, Any]] | None = None,
    participants_msg: str | None = None,
    contacts_msg: str | None = None,
    is_session_bootstrap: bool = False,
) -> AgentInput:
    """Create a test AgentInput."""
    return AgentInput(
        msg=make_platform_message(content),
        tools=FakeAgentTools(),
        history=HistoryProvider(raw=raw_history or []),
        participants_msg=participants_msg,
        contacts_msg=contacts_msg,
        is_session_bootstrap=is_session_bootstrap,
        room_id="room-1",
    )


class TestFrameworkAdapterProtocol:
    """Verify SimpleAdapter implements FrameworkAdapter protocol."""

    def test_implements_protocol(self):
        """SimpleAdapter should implement FrameworkAdapter."""
        adapter = RecordingAdapter()
        assert isinstance(adapter, FrameworkAdapter)


class TestOnEvent:
    """Tests for on_event() dispatch to on_message()."""

    async def test_dispatches_to_on_message(self):
        """on_event should dispatch to on_message."""
        adapter = RecordingAdapter()
        inp = make_agent_input(content="Test message")

        await adapter.on_event(inp)

        assert len(adapter.calls) == 1
        call = adapter.calls[0]
        assert call["msg"].content == "Test message"
        assert call["room_id"] == "room-1"

    async def test_passes_all_fields(self):
        """on_event should pass all AgentInput fields to on_message."""
        adapter = RecordingAdapter()
        inp = make_agent_input(
            content="Hello",
            participants_msg="Alice joined",
            is_session_bootstrap=True,
        )

        await adapter.on_event(inp)

        call = adapter.calls[0]
        assert call["participants_msg"] == "Alice joined"
        assert call["is_session_bootstrap"] is True
        assert isinstance(call["tools"], FakeAgentTools)


class TestHistoryConversion:
    """Tests for history conversion behavior."""

    async def test_converts_history_with_converter(self):
        """Should convert history using provided converter."""
        converter = StringHistoryConverter()
        adapter = RecordingAdapter(history_converter=converter)
        inp = make_agent_input(
            raw_history=[
                {"content": "First"},
                {"content": "Second"},
                {"content": "Third"},
            ]
        )

        await adapter.on_event(inp)

        call = adapter.calls[0]
        assert call["history"] == "First | Second | Third"

    async def test_passes_provider_without_converter(self):
        """Should pass HistoryProvider when no converter set."""
        adapter = RecordingAdapter()  # No converter
        inp = make_agent_input(
            raw_history=[{"content": "Message 1"}, {"content": "Message 2"}]
        )

        await adapter.on_event(inp)

        call = adapter.calls[0]
        assert isinstance(call["history"], HistoryProvider)
        assert len(call["history"]) == 2

    async def test_handles_empty_history(self):
        """Should handle empty history gracefully."""
        converter = StringHistoryConverter()
        adapter = RecordingAdapter(history_converter=converter)
        inp = make_agent_input(raw_history=[])

        await adapter.on_event(inp)

        call = adapter.calls[0]
        assert call["history"] == ""


class TestOnStarted:
    """Tests for on_started() lifecycle hook."""

    async def test_sets_agent_name(self):
        """on_started should set agent_name."""
        adapter = RecordingAdapter()

        await adapter.on_started("TestAgent", "A test agent")

        assert adapter.agent_name == "TestAgent"

    async def test_sets_agent_description(self):
        """on_started should set agent_description."""
        adapter = RecordingAdapter()

        await adapter.on_started("TestAgent", "A test agent")

        assert adapter.agent_description == "A test agent"

    async def test_defaults_empty(self):
        """agent_name and agent_description should default to empty."""
        adapter = RecordingAdapter()

        assert adapter.agent_name == ""
        assert adapter.agent_description == ""


class TestOnCleanup:
    """Tests for on_cleanup() lifecycle hook."""

    async def test_can_be_overridden(self):
        """on_cleanup should be overridable."""
        adapter = RecordingAdapter()

        await adapter.on_cleanup("room-123")

        assert adapter.cleanup_calls == ["room-123"]

    async def test_base_implementation_does_nothing(self):
        """Base on_cleanup should do nothing (not raise)."""

        # Use a minimal adapter that doesn't override on_cleanup
        class MinimalAdapter(SimpleAdapter[str]):
            async def on_message(
                self,
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                *,
                is_session_bootstrap,
                room_id,
            ):
                pass

        adapter = MinimalAdapter()
        # Should not raise
        await adapter.on_cleanup("room-1")


class TestAdapterSubclassing:
    """Tests for different subclassing patterns."""

    async def test_list_history_converter(self):
        """Should work with list history converter."""

        class ListAdapter(SimpleAdapter[list[str]]):
            def __init__(self):
                super().__init__(history_converter=ListHistoryConverter())
                self.received_history: list[str] | None = None

            async def on_message(
                self,
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                *,
                is_session_bootstrap,
                room_id,
            ):
                self.received_history = history

        adapter = ListAdapter()
        inp = make_agent_input(
            raw_history=[{"content": "A"}, {"content": "B"}, {"content": "C"}]
        )

        await adapter.on_event(inp)

        assert adapter.received_history == ["A", "B", "C"]

    async def test_no_converter_passes_provider(self):
        """Adapter without converter should receive HistoryProvider."""

        class NoConverterAdapter(SimpleAdapter[HistoryProvider]):
            def __init__(self):
                super().__init__()  # No converter
                self.received_history = None

            async def on_message(
                self,
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                *,
                is_session_bootstrap,
                room_id,
            ):
                self.received_history = history

        adapter = NoConverterAdapter()
        raw = [{"content": "Test"}]
        inp = make_agent_input(raw_history=raw)

        await adapter.on_event(inp)

        assert isinstance(adapter.received_history, HistoryProvider)
        assert adapter.received_history.raw == raw


class TestMultipleEvents:
    """Tests for handling multiple events."""

    async def test_handles_multiple_events(self):
        """Should handle multiple sequential events."""
        adapter = RecordingAdapter()

        await adapter.on_event(make_agent_input(content="First"))
        await adapter.on_event(make_agent_input(content="Second"))
        await adapter.on_event(make_agent_input(content="Third"))

        assert len(adapter.calls) == 3
        assert adapter.calls[0]["msg"].content == "First"
        assert adapter.calls[1]["msg"].content == "Second"
        assert adapter.calls[2]["msg"].content == "Third"
