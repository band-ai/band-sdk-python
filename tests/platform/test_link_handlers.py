"""BandLinkEventHandlers behavior."""

from __future__ import annotations

from band.client.streaming import (
    MessageCreatedPayload,
    MessageMetadata,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
)
from band.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
)
from band.platform.link import BandLink


class TestBandLinkEventHandlers:
    """Test internal event handlers that queue typed events."""

    async def test_on_room_added_queues_room_added_event(self):
        """_on_room_added() should queue RoomAddedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = RoomAddedPayload(
            id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            title="Test Room",
        )

        await link._on_room_added(payload)

        # Check event was queued
        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomAddedEvent)
        assert event.room_id == "room-123"

    async def test_on_room_removed_queues_room_removed_event(self):
        """_on_room_removed() should queue RoomRemovedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = RoomRemovedPayload(
            id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            title="Test Room",
        )

        await link._on_room_removed(payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomRemovedEvent)
        assert event.room_id == "room-123"

    async def test_on_message_created_queues_message_event(self):
        """_on_message_created() should queue MessageEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = MessageCreatedPayload(
            id="msg-123",
            content="Hello",
            message_type="text",
            sender_id="user-456",
            sender_type="User",
            chat_room_id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            metadata=MessageMetadata(mentions=[], status="sent"),
        )

        await link._on_message_created("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, MessageEvent)
        assert event.room_id == "room-123"
        assert event.payload.content == "Hello"

    async def test_on_participant_added_queues_participant_added_event(self):
        """_on_participant_added() should queue ParticipantAddedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = ParticipantAddedPayload(id="user-123", name="Test User", type="User")

        await link._on_participant_added("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, ParticipantAddedEvent)
        assert event.room_id == "room-123"
        assert event.payload.id == "user-123"

    async def test_on_participant_removed_queues_participant_removed_event(self):
        """_on_participant_removed() should queue ParticipantRemovedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = ParticipantRemovedPayload(
            id="user-123", name="Test User", type="User"
        )

        await link._on_participant_removed("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, ParticipantRemovedEvent)
        assert event.room_id == "room-123"

    async def test_on_room_deleted_queues_room_deleted_event(self):
        """_on_room_deleted() should queue RoomDeletedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = RoomDeletedPayload(id="room-123")

        await link._on_room_deleted("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomDeletedEvent)
        assert event.room_id == "room-123"
        assert event.payload.id == "room-123"
