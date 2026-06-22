"""Tests for contacts_msg in preprocessing."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from band.platform.event import MessageEvent
from band.client.streaming import MessageCreatedPayload, MessageMetadata, Mention
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext


@pytest.fixture
def mock_execution_context():
    """Create mock ExecutionContext."""
    ctx = MagicMock(spec=ExecutionContext)
    ctx.room_id = "room-123"
    ctx.config = MagicMock()
    ctx.config.enable_context_hydration = False
    ctx.is_llm_initialized = False
    ctx.participants = [
        {
            "id": "agent-123",
            "name": "Test Agent",
            "type": "Agent",
            "handle": "alice/test-agent",
        }
    ]
    ctx.get_pending_system_messages = MagicMock(return_value=[])
    ctx.mark_llm_initialized = MagicMock()
    # AgentTools.from_context needs ctx.link.rest
    ctx.link = MagicMock()
    ctx.link.rest = MagicMock()
    return ctx


@pytest.fixture
def sample_message_event():
    """Create sample MessageEvent."""
    return MessageEvent(
        room_id="room-123",
        payload=MessageCreatedPayload(
            id="msg-123",
            content="Hello",
            message_type="text",
            sender_type="User",
            sender_id="user-456",
            sender_name="Alice",
            chat_room_id="room-123",
            metadata=MessageMetadata(
                mentions=[Mention(id="agent-123", handle="alice/test-agent")],
                status="sent",
            ),
            inserted_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        ),
        raw={},
    )


class TestPreprocessorContactsMsg:
    """Tests for contacts_msg in DefaultPreprocessor."""

    async def test_preprocessor_includes_contacts_msg(
        self, mock_execution_context, sample_message_event
    ):
        """AgentInput should have contacts_msg field."""
        mock_execution_context.get_pending_system_messages = MagicMock(
            return_value=["[Contacts]: @bob is now a contact"]
        )

        preprocessor = DefaultPreprocessor()
        result = await preprocessor.process(
            mock_execution_context, sample_message_event, "agent-123"
        )

        assert result is not None
        assert result.contacts_msg == "[Contacts]: @bob is now a contact"

    async def test_preprocessor_drains_from_context(
        self, mock_execution_context, sample_message_event
    ):
        """Preprocessor should call get_pending_system_messages."""
        mock_execution_context.get_pending_system_messages = MagicMock(
            return_value=["test message"]
        )

        preprocessor = DefaultPreprocessor()
        await preprocessor.process(
            mock_execution_context, sample_message_event, "agent-123"
        )

        mock_execution_context.get_pending_system_messages.assert_called_once()

    async def test_preprocessor_contacts_msg_none_when_empty(
        self, mock_execution_context, sample_message_event
    ):
        """contacts_msg should be None if no updates."""
        mock_execution_context.get_pending_system_messages = MagicMock(return_value=[])

        preprocessor = DefaultPreprocessor()
        result = await preprocessor.process(
            mock_execution_context, sample_message_event, "agent-123"
        )

        assert result is not None
        assert result.contacts_msg is None

    async def test_preprocessor_contacts_msg_multiple(
        self, mock_execution_context, sample_message_event
    ):
        """Multiple updates should be joined with newline."""
        mock_execution_context.get_pending_system_messages = MagicMock(
            return_value=[
                "[Contacts]: @alice is now a contact",
                "[Contacts]: @bob is now a contact",
                "[Contacts]: Contact @charlie was removed",
            ]
        )

        preprocessor = DefaultPreprocessor()
        result = await preprocessor.process(
            mock_execution_context, sample_message_event, "agent-123"
        )

        assert result is not None
        assert result.contacts_msg is not None
        assert "@alice" in result.contacts_msg
        assert "@bob" in result.contacts_msg
        assert "@charlie" in result.contacts_msg
        assert result.contacts_msg.count("\n") == 2


class TestDrainSystemMessages:
    """Tests for _drain_system_messages helper."""

    def test_drain_returns_none_when_empty(self, mock_execution_context):
        """Should return None if no messages."""
        mock_execution_context.get_pending_system_messages = MagicMock(return_value=[])

        preprocessor = DefaultPreprocessor()
        result = preprocessor._drain_system_messages(mock_execution_context)

        assert result is None

    def test_drain_joins_multiple_messages(self, mock_execution_context):
        """Should join multiple messages with newlines."""
        mock_execution_context.get_pending_system_messages = MagicMock(
            return_value=["Message 1", "Message 2", "Message 3"]
        )

        preprocessor = DefaultPreprocessor()
        result = preprocessor._drain_system_messages(mock_execution_context)

        assert result == "Message 1\nMessage 2\nMessage 3"

    def test_drain_single_message(self, mock_execution_context):
        """Should return single message without newline."""
        mock_execution_context.get_pending_system_messages = MagicMock(
            return_value=["Single message"]
        )

        preprocessor = DefaultPreprocessor()
        result = preprocessor._drain_system_messages(mock_execution_context)

        assert result == "Single message"
