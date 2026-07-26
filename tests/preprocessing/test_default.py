"""Tests for DefaultPreprocessor."""

from unittest.mock import AsyncMock, MagicMock, patch


from band.client.streaming import MessageCreatedPayload, MessageMetadata
from band.core.protocols import Preprocessor
from band.core.types import AgentInput, HistoryProvider
from band.platform.event import (
    MessageEvent,
    RoomAddedEvent,
    ParticipantAddedEvent,
)
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.types import SessionConfig


def make_message_payload(
    *,
    id: str = "msg-1",
    content: str = "Hello",
    sender_id: str = "user-1",
    sender_type: str = "User",
    room_id: str = "room-1",
    message_type: str = "text",
) -> MessageCreatedPayload:
    """Create test MessageCreatedPayload."""
    return MessageCreatedPayload(
        id=id,
        content=content,
        message_type=message_type,
        metadata=MessageMetadata(mentions=[], status="sent"),
        sender_id=sender_id,
        sender_type=sender_type,
        chat_room_id=room_id,
        thread_id=None,
        inserted_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


def make_message_event(
    *,
    room_id: str = "room-1",
    content: str = "Hello",
    sender_id: str = "user-1",
    sender_type: str = "User",
) -> MessageEvent:
    """Create test MessageEvent."""
    return MessageEvent(
        room_id=room_id,
        payload=make_message_payload(
            content=content,
            sender_id=sender_id,
            sender_type=sender_type,
            room_id=room_id,
        ),
    )


def make_mock_ctx(
    *,
    room_id: str = "room-1",
    is_llm_initialized: bool = False,
    enable_context_hydration: bool = True,
    participants_changed: bool = False,
    history_messages: list | None = None,
):
    """Create mock ExecutionContext."""
    ctx = MagicMock()
    ctx.room_id = room_id
    ctx.is_llm_initialized = is_llm_initialized
    ctx.config = SessionConfig(enable_context_hydration=enable_context_hydration)
    ctx.participants = [{"id": "user-1", "name": "Alice", "type": "User"}]
    ctx.participants_changed = MagicMock(return_value=participants_changed)
    ctx.mark_llm_initialized = MagicMock()
    ctx.mark_participants_sent = MagicMock()
    ctx.get_context = AsyncMock(
        return_value=MagicMock(
            messages=history_messages or [],
            participants=[],
        )
    )
    ctx.link = MagicMock()
    ctx.link.rest = MagicMock()
    return ctx


class TestPreprocessorProtocol:
    """Verify DefaultPreprocessor implements Preprocessor protocol."""

    def test_implements_protocol(self):
        """DefaultPreprocessor should implement Preprocessor protocol."""
        preprocessor = DefaultPreprocessor()
        assert isinstance(preprocessor, Preprocessor)


class TestEventFiltering:
    """Tests for event type filtering."""

    async def test_returns_none_for_room_added_event(self):
        """Should return None for RoomAddedEvent."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = RoomAddedEvent(room_id="room-1")

        result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is None

    async def test_returns_none_for_participant_added_event(self):
        """Should return None for ParticipantAddedEvent."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = ParticipantAddedEvent(room_id="room-1")

        result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is None

    async def test_returns_none_for_message_with_none_payload(self):
        """Should return None when MessageEvent payload is None."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = MessageEvent(room_id="room-1", payload=None)

        result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is None


class TestSelfMessageFiltering:
    """Tests for self-message filtering."""

    async def test_skips_own_agent_messages(self):
        """Should skip messages from the same agent."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event(sender_id="agent-1", sender_type="Agent")

        result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is None

    async def test_processes_other_agent_messages(self):
        """Should process messages from different agents."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event(sender_id="agent-2", sender_type="Agent")

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is not None
        assert isinstance(result, AgentInput)

    async def test_processes_user_messages(self):
        """Should process messages from users."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event(sender_id="user-1", sender_type="User")

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is not None
        assert isinstance(result, AgentInput)


class TestAgentInputConstruction:
    """Tests for AgentInput construction from MessageEvent."""

    async def test_creates_platform_message_correctly(self):
        """Should convert MessageEvent to PlatformMessage."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event(
            content="Test content",
            room_id="room-123",
            sender_id="user-456",
            sender_type="User",
        )

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is not None
        assert result.msg.content == "Test content"
        assert result.msg.room_id == "room-123"
        assert result.msg.sender_id == "user-456"
        assert result.msg.sender_type == "User"
        assert result.room_id == "room-123"

    async def test_translates_uuid_mentions_in_current_message(self):
        """@[[uuid]] mention tokens become @handle, like history formatting.

        The platform normalizes mentions to ``@[[uuid]]`` in stored content; a
        raw uuid in the live turn misleads the LLM (e.g. into using it as a
        memory ``subject_id``), so the current message gets the same
        translation history already gets.
        """
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        ctx.participants = [
            {"id": "agent-9", "name": "Bot", "type": "Agent", "handle": "org/bot"},
            {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
        ]
        event = make_message_event(
            content="@[[agent-9]] please remember this about me",
            sender_id="user-1",
        )

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is not None
        assert result.msg.content == "@org/bot please remember this about me"

    async def test_unknown_mention_uuid_is_left_intact(self):
        """A mention of someone missing from the roster stays untranslated."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event(content="@[[stranger-7]] hello")

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result is not None
        assert result.msg.content == "@[[stranger-7]] hello"

    async def test_sets_history_provider(self):
        """Should set HistoryProvider on AgentInput."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert isinstance(result.history, HistoryProvider)


class TestSessionBootstrap:
    """Tests for session bootstrap detection."""

    async def test_detects_session_bootstrap(self):
        """Should set is_session_bootstrap=True when not initialized."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(is_llm_initialized=False)
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result.is_session_bootstrap is True

    async def test_not_bootstrap_when_initialized(self):
        """Should set is_session_bootstrap=False when already initialized."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(is_llm_initialized=True)
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result.is_session_bootstrap is False

    async def test_marks_llm_initialized_on_bootstrap(self):
        """Should mark LLM as initialized on bootstrap."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(is_llm_initialized=False)
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                await preprocessor.process(ctx, event, agent_id="agent-1")

        ctx.mark_llm_initialized.assert_called_once()

    async def test_does_not_mark_initialized_when_already_initialized(self):
        """Should not mark initialized again if already initialized."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(is_llm_initialized=True)
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                await preprocessor.process(ctx, event, agent_id="agent-1")

        ctx.mark_llm_initialized.assert_not_called()


class TestHistoryLoading:
    """Tests for history loading behavior."""

    async def test_loads_history_on_bootstrap_with_hydration_enabled(self):
        """Should load history on bootstrap when enable_context_hydration=True."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(
            is_llm_initialized=False,
            enable_context_hydration=True,
            history_messages=[
                {"id": "msg-0", "content": "Previous message"},
            ],
        )
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                with patch(
                    "band.preprocessing.default.format_history_for_llm"
                ) as mock_format:
                    mock_format.return_value = [{"role": "user", "content": "Previous"}]
                    result = await preprocessor.process(ctx, event, agent_id="agent-1")

        # Should have called get_context
        ctx.get_context.assert_called_once()
        # Should have formatted history
        mock_format.assert_called_once()
        # History should be in provider
        assert len(result.history) == 1

    async def test_skips_history_loading_with_hydration_disabled(self):
        """Should skip history loading when enable_context_hydration=False."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(
            is_llm_initialized=False,
            enable_context_hydration=False,
        )
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        # Should NOT have called get_context
        ctx.get_context.assert_not_called()
        # History should be empty
        assert len(result.history) == 0

    async def test_no_history_loading_when_not_bootstrap(self):
        """Should not load history when already initialized."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(
            is_llm_initialized=True,
            enable_context_hydration=True,
        )
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        # Should NOT have called get_context
        ctx.get_context.assert_not_called()
        # History should be empty
        assert len(result.history) == 0


class TestParticipantsHandling:
    """Tests for participant change handling."""

    async def test_passes_participants_msg_when_changed(self):
        """Should include participants_msg when participants changed."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(participants_changed=True)
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = "Alice joined the room"
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result.participants_msg == "Alice joined the room"

    async def test_passes_none_when_no_changes(self):
        """Should pass None when no participant changes."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(participants_changed=False)
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        assert result.participants_msg is None


class TestAgentToolsCreation:
    """Tests for AgentTools creation."""

    async def test_creates_tools_from_context(self):
        """Should create AgentTools from ExecutionContext."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx()
        event = make_message_event()

        mock_agent_tools = MagicMock()
        with patch("band.preprocessing.default.AgentTools") as mock_tools_cls:
            mock_tools_cls.from_context.return_value = mock_agent_tools
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        mock_tools_cls.from_context.assert_called_once_with(ctx)
        assert result.tools == mock_agent_tools


class TestHistoryLoadingErrors:
    """Tests for error handling in history loading."""

    async def test_handles_history_loading_error(self):
        """Should return empty history on loading error."""
        preprocessor = DefaultPreprocessor()
        ctx = make_mock_ctx(
            is_llm_initialized=False,
            enable_context_hydration=True,
        )
        ctx.get_context = AsyncMock(side_effect=Exception("API error"))
        event = make_message_event()

        with patch("band.preprocessing.default.AgentTools") as mock_tools:
            mock_tools.from_context.return_value = MagicMock()
            with patch(
                "band.preprocessing.default.check_and_format_participants"
            ) as mock_participants:
                mock_participants.return_value = None
                result = await preprocessor.process(ctx, event, agent_id="agent-1")

        # Should still return AgentInput with empty history
        assert result is not None
        assert len(result.history) == 0


class TestBootstrapHistoryTruncation:
    """Backlog messages that arrived after the trigger must not enter bootstrap
    history: they are pending turns of their own, and replaying them exposes
    future requests and duplicates them when FIFO processing reaches them."""

    async def test_history_stops_strictly_before_the_trigger(self):
        history_messages = [
            {"id": "old-1", "content": "earlier question", "message_type": "text"},
            {"id": "msg-1", "content": "Hello", "message_type": "text"},  # trigger
            {
                "id": "backlog-1",
                "content": "a later pending ask",
                "message_type": "text",
            },
        ]
        ctx = make_mock_ctx(history_messages=history_messages)
        preprocessor = DefaultPreprocessor()

        inp = await preprocessor.process(ctx, make_message_event(), agent_id="agent-1")

        contents = [m["content"] for m in inp.history.raw]
        assert contents == ["earlier question"], (
            "the trigger and later backlog must not be replayed as history"
        )
