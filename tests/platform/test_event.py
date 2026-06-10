"""Tests for platform events using tagged union pattern."""

from band.platform.event import (
    MessageEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    PlatformEvent,
)
from band.client.streaming import (
    MessageCreatedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    MessageMetadata,
)


class TestMessageEvent:
    """Test MessageEvent construction and usage."""

    def test_construct_message_event(self):
        """Construct a MessageEvent with typed payload."""
        payload = MessageCreatedPayload(
            id="msg-456",
            content="Hello",
            message_type="text",
            sender_id="user-789",
            sender_type="User",
            chat_room_id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            metadata=MessageMetadata(mentions=[]),
        )

        event = MessageEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.type == "message_created"
        assert event.room_id == "room-123"
        assert event.payload.content == "Hello"
        assert event.payload.id == "msg-456"
        assert event.raw is None

    def test_message_metadata_optional(self):
        """MessageMetadata is optional (websocket may omit it)."""
        payload = MessageCreatedPayload(
            id="msg-no-metadata",
            content="Hello without metadata",
            message_type="text",
            sender_id="user-789",
            sender_type="User",
            chat_room_id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        event = MessageEvent(room_id="room-123", payload=payload)
        assert event.payload.metadata is None

    def test_message_event_isinstance(self):
        """Test isinstance check for MessageEvent."""
        payload = MessageCreatedPayload(
            id="msg-1",
            content="Test",
            message_type="text",
            sender_id="user-1",
            sender_type="User",
            chat_room_id="room-1",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            metadata=MessageMetadata(mentions=[]),
        )

        event = MessageEvent(room_id="room-1", payload=payload)

        assert isinstance(event, MessageEvent)
        assert not isinstance(event, RoomAddedEvent)
        assert not isinstance(event, RoomRemovedEvent)


class TestRoomAddedEvent:
    """Test RoomAddedEvent construction and usage."""

    def test_construct_room_added_event(self):
        """Construct a RoomAddedEvent with typed payload."""
        payload = RoomAddedPayload(
            id="room-123",
            title="Test Room",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        event = RoomAddedEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.type == "room_added"
        assert event.room_id == "room-123"
        assert event.payload.title == "Test Room"
        assert event.payload.id == "room-123"

    def test_room_added_event_isinstance(self):
        """Test isinstance check for RoomAddedEvent."""
        payload = RoomAddedPayload(
            id="room-1",
            title="Room",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        event = RoomAddedEvent(room_id="room-1", payload=payload)

        assert isinstance(event, RoomAddedEvent)
        assert not isinstance(event, MessageEvent)
        assert not isinstance(event, RoomRemovedEvent)


class TestRoomRemovedEvent:
    """Test RoomRemovedEvent construction and usage."""

    def test_construct_room_removed_event(self):
        """Construct a RoomRemovedEvent with typed payload."""
        payload = RoomRemovedPayload(
            id="room-123",
            status="removed",
            type="direct",
            title="Test Room",
            removed_at="2024-01-01T00:00:00Z",
        )

        event = RoomRemovedEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.type == "room_removed"
        assert event.room_id == "room-123"
        assert event.payload.status == "removed"

    def test_room_removed_event_isinstance(self):
        """Test isinstance check for RoomRemovedEvent."""
        payload = RoomRemovedPayload(
            id="room-1",
            status="removed",
            type="direct",
            title="Room",
            removed_at="2024-01-01T00:00:00Z",
        )

        event = RoomRemovedEvent(room_id="room-1", payload=payload)

        assert isinstance(event, RoomRemovedEvent)
        assert not isinstance(event, MessageEvent)
        assert not isinstance(event, RoomAddedEvent)


class TestRoomDeletedEvent:
    """Test RoomDeletedEvent construction and usage."""

    def test_construct_room_deleted_event(self):
        """Construct a RoomDeletedEvent with typed payload."""
        payload = RoomDeletedPayload(id="room-123")

        event = RoomDeletedEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.type == "room_deleted"
        assert event.room_id == "room-123"
        assert event.payload.id == "room-123"

    def test_room_deleted_event_isinstance(self):
        """Test isinstance check for RoomDeletedEvent."""
        event = RoomDeletedEvent(
            room_id="room-1",
            payload=RoomDeletedPayload(id="room-1"),
        )

        assert isinstance(event, RoomDeletedEvent)
        assert not isinstance(event, MessageEvent)
        assert not isinstance(event, RoomAddedEvent)


class TestParticipantEvents:
    """Test ParticipantAddedEvent and ParticipantRemovedEvent."""

    def test_construct_participant_added_event(self):
        """Construct a ParticipantAddedEvent with typed payload."""
        payload = ParticipantAddedPayload(
            id="user-123",
            name="Test User",
            type="User",
            is_remote=True,
            is_external=True,
        )

        event = ParticipantAddedEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.type == "participant_added"
        assert event.room_id == "room-123"
        assert event.payload.id == "user-123"
        assert event.payload.name == "Test User"
        assert event.payload.is_remote is True
        assert event.payload.is_external is True

    def test_construct_participant_added_event_from_legacy_alias(self):
        """Legacy participant payloads should still expose is_remote."""
        payload = ParticipantAddedPayload(
            id="user-123",
            name="Test User",
            type="User",
            is_external=False,
        )

        event = ParticipantAddedEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.payload.is_remote is False
        assert event.payload.is_external is False

    def test_construct_participant_removed_event(self):
        """Construct a ParticipantRemovedEvent with typed payload."""
        payload = ParticipantRemovedPayload(id="user-123")

        event = ParticipantRemovedEvent(
            room_id="room-123",
            payload=payload,
        )

        assert event.type == "participant_removed"
        assert event.room_id == "room-123"
        assert event.payload.id == "user-123"

    def test_participant_event_isinstance(self):
        """Test isinstance checks for participant events."""
        added = ParticipantAddedEvent(
            room_id="room-1",
            payload=ParticipantAddedPayload(id="user-1", name="User", type="User"),
        )
        removed = ParticipantRemovedEvent(
            room_id="room-1",
            payload=ParticipantRemovedPayload(id="user-1"),
        )

        assert isinstance(added, ParticipantAddedEvent)
        assert isinstance(removed, ParticipantRemovedEvent)
        assert not isinstance(added, MessageEvent)
        assert not isinstance(removed, RoomAddedEvent)


class TestEventPatternMatching:
    """Test pattern matching with match statements."""

    def test_match_message_event(self):
        """Match statement correctly identifies MessageEvent."""
        payload = MessageCreatedPayload(
            id="msg-1",
            content="Test",
            message_type="text",
            sender_id="user-1",
            sender_type="User",
            chat_room_id="room-1",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            metadata=MessageMetadata(mentions=[]),
        )

        event: PlatformEvent = MessageEvent(room_id="room-1", payload=payload)
        result = None

        match event:
            case MessageEvent(payload=msg):
                result = f"message: {msg.content}"
            case RoomAddedEvent():
                result = "room_added"
            case _:
                result = "unknown"

        assert result == "message: Test"

    def test_match_room_added_event(self):
        """Match statement correctly identifies RoomAddedEvent."""
        payload = RoomAddedPayload(
            id="room-1",
            title="Room",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        event: PlatformEvent = RoomAddedEvent(room_id="room-1", payload=payload)
        result = None

        match event:
            case MessageEvent():
                result = "message"
            case RoomAddedEvent(room_id=rid):
                result = f"room_added: {rid}"
            case _:
                result = "unknown"

        assert result == "room_added: room-1"

    def test_match_with_multiple_events(self):
        """Match statement works with multiple event types."""
        events = [
            MessageEvent(
                room_id="room-1",
                payload=MessageCreatedPayload(
                    id="msg-1",
                    content="Hello",
                    message_type="text",
                    sender_id="user-1",
                    sender_type="User",
                    chat_room_id="room-1",
                    inserted_at="2024-01-01T00:00:00Z",
                    updated_at="2024-01-01T00:00:00Z",
                    metadata=MessageMetadata(mentions=[]),
                ),
            ),
            RoomAddedEvent(
                room_id="room-1",
                payload=RoomAddedPayload(
                    id="room-1",
                    title="Room",
                    inserted_at="2024-01-01T00:00:00Z",
                    updated_at="2024-01-01T00:00:00Z",
                ),
            ),
            RoomDeletedEvent(
                room_id="room-1",
                payload=RoomDeletedPayload(id="room-1"),
            ),
            ParticipantAddedEvent(
                room_id="room-1",
                payload=ParticipantAddedPayload(
                    id="user-2", name="User 2", type="User"
                ),
            ),
        ]

        results = []
        for event in events:
            match event:
                case MessageEvent():
                    results.append("message")
                case RoomAddedEvent():
                    results.append("room_added")
                case RoomDeletedEvent():
                    results.append("room_deleted")
                case ParticipantAddedEvent():
                    results.append("participant_added")
                case _:
                    results.append("unknown")

        assert results == [
            "message",
            "room_added",
            "room_deleted",
            "participant_added",
        ]


class TestEventWithRawPayload:
    """Test events with raw WebSocket payload."""

    def test_event_with_raw_payload(self):
        """Event can store raw WebSocket payload for debugging."""
        raw = {
            "channel": "chat_room:room-123",
            "event": "message_created",
            "payload": {},
        }

        payload = MessageCreatedPayload(
            id="msg-1",
            content="Test",
            message_type="text",
            sender_id="user-1",
            sender_type="User",
            chat_room_id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            metadata=MessageMetadata(mentions=[]),
        )

        event = MessageEvent(
            room_id="room-123",
            payload=payload,
            raw=raw,
        )

        assert event.raw == raw
