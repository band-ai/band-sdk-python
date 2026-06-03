"""Unit tests for contact event types and pattern matching."""

from band.client.streaming import (
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)
from band.platform.event import (
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
    PlatformEvent,
)


class TestContactRequestReceivedEvent:
    """Tests for ContactRequestReceivedEvent."""

    def test_construct_event(self):
        """Should create event with payload."""
        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
        )
        event = ContactRequestReceivedEvent(payload=payload)

        assert event.type == "contact_request_received"
        assert event.room_id is None  # Contact events have no room
        assert event.payload.id == "req-123"

    def test_default_type(self):
        """Should have correct default type literal."""
        event = ContactRequestReceivedEvent()
        assert event.type == "contact_request_received"

    def test_isinstance_contact_event(self):
        """Should be recognized as part of ContactEvent union."""
        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
        )
        event = ContactRequestReceivedEvent(payload=payload)

        # Runtime check via tuple
        contact_event_types = (
            ContactRequestReceivedEvent,
            ContactRequestUpdatedEvent,
            ContactAddedEvent,
            ContactRemovedEvent,
        )
        assert isinstance(event, contact_event_types)


class TestContactRequestUpdatedEvent:
    """Tests for ContactRequestUpdatedEvent."""

    def test_construct_event(self):
        """Should create event with payload."""
        payload = ContactRequestUpdatedPayload(
            id="req-123",
            status="approved",
        )
        event = ContactRequestUpdatedEvent(payload=payload)

        assert event.type == "contact_request_updated"
        assert event.payload.status == "approved"

    def test_rejected_status(self):
        """Should handle rejected status."""
        payload = ContactRequestUpdatedPayload(
            id="req-123",
            status="rejected",
        )
        event = ContactRequestUpdatedEvent(payload=payload)
        assert event.payload.status == "rejected"


class TestContactAddedEvent:
    """Tests for ContactAddedEvent."""

    def test_construct_event(self):
        """Should create event with payload."""
        payload = ContactAddedPayload(
            id="contact-123",
            handle="jane_smith",
            name="Jane Smith",
            type="User",
            inserted_at="2026-02-09T10:35:00Z",
        )
        event = ContactAddedEvent(payload=payload)

        assert event.type == "contact_added"
        assert event.payload.name == "Jane Smith"

    def test_agent_contact(self):
        """Should handle agent contact type."""
        payload = ContactAddedPayload(
            id="contact-456",
            handle="weather-bot",
            name="Weather Bot",
            type="Agent",
            description="Weather forecasts",
            is_external=True,
            inserted_at="2026-02-09T10:35:00Z",
        )
        event = ContactAddedEvent(payload=payload)
        assert event.payload.type == "Agent"
        assert event.payload.is_external is True


class TestContactRemovedEvent:
    """Tests for ContactRemovedEvent."""

    def test_construct_event(self):
        """Should create event with payload."""
        payload = ContactRemovedPayload(id="contact-123")
        event = ContactRemovedEvent(payload=payload)

        assert event.type == "contact_removed"
        assert event.payload.id == "contact-123"


class TestContactEventPatternMatching:
    """Test pattern matching with contact events."""

    def test_match_contact_request_received(self):
        """Should match contact_request_received event."""
        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
        )
        event: PlatformEvent = ContactRequestReceivedEvent(payload=payload)

        match event:
            case ContactRequestReceivedEvent(payload=p):
                result = f"request from {p.from_name}"
            case _:
                result = "unknown"

        assert result == "request from John Doe"

    def test_match_contact_request_updated(self):
        """Should match contact_request_updated event."""
        payload = ContactRequestUpdatedPayload(
            id="req-123",
            status="approved",
        )
        event: PlatformEvent = ContactRequestUpdatedEvent(payload=payload)

        match event:
            case ContactRequestUpdatedEvent(payload=p):
                result = f"request {p.id} -> {p.status}"
            case _:
                result = "unknown"

        assert result == "request req-123 -> approved"

    def test_match_contact_added(self):
        """Should match contact_added event."""
        payload = ContactAddedPayload(
            id="contact-123",
            handle="jane_smith",
            name="Jane Smith",
            type="User",
            inserted_at="2026-02-09T10:35:00Z",
        )
        event: PlatformEvent = ContactAddedEvent(payload=payload)

        match event:
            case ContactAddedEvent(payload=p):
                result = f"new contact: {p.name}"
            case _:
                result = "unknown"

        assert result == "new contact: Jane Smith"

    def test_match_contact_removed(self):
        """Should match contact_removed event."""
        payload = ContactRemovedPayload(id="contact-123")
        event: PlatformEvent = ContactRemovedEvent(payload=payload)

        match event:
            case ContactRemovedEvent(payload=p):
                result = f"removed: {p.id}"
            case _:
                result = "unknown"

        assert result == "removed: contact-123"

    def test_isinstance_tuple_for_contact_events(self):
        """Test using isinstance with tuple for ContactEvent union check."""
        events = [
            ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id="r1",
                    from_handle="h",
                    from_name="n",
                    status="pending",
                    inserted_at="2026-01-01T00:00:00Z",
                )
            ),
            ContactRequestUpdatedEvent(
                payload=ContactRequestUpdatedPayload(id="r1", status="approved")
            ),
            ContactAddedEvent(
                payload=ContactAddedPayload(
                    id="c1",
                    handle="h",
                    name="n",
                    type="User",
                    inserted_at="2026-01-01T00:00:00Z",
                )
            ),
            ContactRemovedEvent(payload=ContactRemovedPayload(id="c1")),
        ]

        contact_event_types = (
            ContactRequestReceivedEvent,
            ContactRequestUpdatedEvent,
            ContactAddedEvent,
            ContactRemovedEvent,
        )

        for event in events:
            assert isinstance(event, contact_event_types)

    def test_contact_events_have_none_room_id(self):
        """All contact events should have room_id=None by default."""
        events = [
            ContactRequestReceivedEvent(),
            ContactRequestUpdatedEvent(),
            ContactAddedEvent(),
            ContactRemovedEvent(),
        ]

        for event in events:
            assert event.room_id is None
