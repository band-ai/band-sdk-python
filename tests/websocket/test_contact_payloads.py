"""Unit tests for contact WebSocket payload models."""

import pytest
from pydantic import ValidationError

from band.client.streaming import (
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)


class TestContactRequestReceivedPayload:
    """Tests for ContactRequestReceivedPayload model."""

    def test_valid_payload(self):
        """Should accept all required and optional fields."""
        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            message="Hi there!",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
        )
        assert payload.id == "req-123"
        assert payload.from_handle == "john_doe"
        assert payload.from_name == "John Doe"
        assert payload.message == "Hi there!"
        assert payload.status == "pending"

    def test_optional_message(self):
        """Should allow message to be None."""
        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
        )
        assert payload.message is None

    def test_missing_required_field(self):
        """Should reject payload missing required fields."""
        with pytest.raises(ValidationError):
            ContactRequestReceivedPayload(
                id="req-123",
                # missing from_handle, from_name, status, inserted_at
            )

    def test_extra_fields_allowed(self):
        """Should allow extra fields for forward compatibility."""
        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
            future_field="allowed",
        )
        assert payload.id == "req-123"


class TestContactRequestUpdatedPayload:
    """Tests for ContactRequestUpdatedPayload model."""

    def test_valid_payload(self):
        """Should accept id and status."""
        payload = ContactRequestUpdatedPayload(
            id="req-123",
            status="approved",
        )
        assert payload.id == "req-123"
        assert payload.status == "approved"

    def test_rejected_status(self):
        """Should accept rejected status."""
        payload = ContactRequestUpdatedPayload(
            id="req-123",
            status="rejected",
        )
        assert payload.status == "rejected"

    def test_missing_required_field(self):
        """Should reject payload missing status."""
        with pytest.raises(ValidationError):
            ContactRequestUpdatedPayload(id="req-123")


class TestContactAddedPayload:
    """Tests for ContactAddedPayload model."""

    def test_user_contact(self):
        """Should accept user contact with minimal fields."""
        payload = ContactAddedPayload(
            id="contact-123",
            handle="jane_smith",
            name="Jane Smith",
            type="User",
            inserted_at="2026-02-09T10:35:00Z",
        )
        assert payload.id == "contact-123"
        assert payload.type == "User"
        assert payload.description is None
        assert payload.is_remote is None
        assert payload.is_external is None

    def test_agent_contact(self):
        """Should accept agent contact with all fields."""
        payload = ContactAddedPayload(
            id="contact-456",
            handle="bob/weather-bot",
            name="Weather Bot",
            type="Agent",
            description="Provides weather forecasts",
            is_remote=True,
            is_external=True,
            inserted_at="2026-02-09T10:35:00Z",
        )
        assert payload.type == "Agent"
        assert payload.description == "Provides weather forecasts"
        assert payload.is_remote is True
        assert payload.is_external is True

    def test_internal_agent_contact(self):
        """Should accept internal agent contact."""
        payload = ContactAddedPayload(
            id="contact-789",
            handle="my-agent",
            name="My Agent",
            type="Agent",
            is_remote=False,
            is_external=False,
            inserted_at="2026-02-09T10:35:00Z",
        )
        assert payload.is_remote is False
        assert payload.is_external is False

    def test_missing_required_field(self):
        """Should reject payload missing required fields."""
        with pytest.raises(ValidationError):
            ContactAddedPayload(
                id="contact-123",
                # missing handle, name, type, inserted_at
            )


class TestContactRemovedPayload:
    """Tests for ContactRemovedPayload model."""

    def test_valid_payload(self):
        """Should accept id."""
        payload = ContactRemovedPayload(id="contact-123")
        assert payload.id == "contact-123"

    def test_missing_id(self):
        """Should reject payload without id."""
        with pytest.raises(ValidationError):
            ContactRemovedPayload()

    def test_extra_fields_allowed(self):
        """Should allow extra fields for forward compatibility."""
        payload = ContactRemovedPayload(
            id="contact-123",
            extra_info="some_value",
        )
        assert payload.id == "contact-123"
