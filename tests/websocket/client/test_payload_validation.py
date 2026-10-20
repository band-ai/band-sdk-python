"""WebSocketClient payload validation: malformed events are logged, counted and
skipped rather than crashing the connection; valid ones hydrate into models."""

from __future__ import annotations

import logging

import pytest

import band.client.streaming.wire as wire_module
from band.client.streaming import (
    MessageCreatedPayload,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    WebSocketClient,
    WireEvent,
)
from tests.websocket.client.kit import (
    VALID_MESSAGE_CREATED_PAYLOAD,
    VALID_MESSAGE_UPDATED_PAYLOAD,
    dispatch,
)


class TestPayloadValidation:
    async def test_skips_invalid_message_created_payload(self, caplog):
        """Should log error and skip when message_created payload is missing required fields."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            # Missing: content, sender_id, sender_type, etc.
            received = await dispatch(client, "message_created", {"id": "msg-123"})

        assert received is None, "Callback should not be called for invalid payload"
        assert "Invalid message_created payload" in caplog.text

    async def test_skips_invalid_room_added_payload(self, caplog):
        """Should log error and skip when room_added payload is missing required fields."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            # Missing required fields: id, inserted_at, updated_at
            received = await dispatch(client, "room_added", {"title": "Test Room"})

        assert received is None, "Callback should not be called for invalid payload"
        assert "Invalid room_added payload" in caplog.text

    async def test_rejects_room_added_missing_timestamps(self, caplog):
        """room_added without inserted_at/updated_at must be rejected."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            # Missing required: inserted_at, updated_at
            received = await dispatch(
                client, "room_added", {"id": "room-123", "title": "Test Room"}
            )

        assert received is None, "Callback should not be called without timestamps"
        assert "Invalid room_added payload" in caplog.text

    async def test_skips_invalid_room_removed_payload(self, caplog):
        """Should log error and skip when room_removed payload is missing required fields."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            # Missing required field: id
            received = await dispatch(client, "room_removed", {"status": "closed"})

        assert received is None, "Callback should not be called for invalid payload"
        assert "Invalid room_removed payload" in caplog.text

    async def test_skips_invalid_room_deleted_payload(self, caplog):
        """Should log error and skip when room_deleted payload is missing required fields."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            received = await dispatch(client, "room_deleted", {})

        assert received is None, "Callback should not be called for invalid payload"
        assert "Invalid room_deleted payload" in caplog.text

    async def test_skips_invalid_participant_added_payload(self, caplog):
        """Should log error and skip when participant_added payload is missing required fields."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            # Missing required fields: name, type (only id is provided)
            received = await dispatch(client, "participant_added", {"id": "p-123"})

        assert received is None, "Callback should not be called for invalid payload"
        assert "Invalid participant_added payload" in caplog.text

    async def test_skips_invalid_participant_removed_payload(self, caplog):
        """Should log error and skip when participant_removed payload is missing required fields."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.ERROR):
            # Missing: id
            received = await dispatch(client, "participant_removed", {})

        assert received is None, "Callback should not be called for invalid payload"
        assert "Invalid participant_removed payload" in caplog.text

    async def test_accepts_valid_message_created_payload(self):
        """Should accept valid message_created payload without raising."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client, "message_created", VALID_MESSAGE_CREATED_PAYLOAD
        )
        assert isinstance(received, MessageCreatedPayload)
        assert received.id == "msg-123"

    async def test_accepts_message_updated_payload_with_delivery_status(self):
        """message_updated parses into MessageCreatedPayload and exposes delivery_status."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client, "message_updated", VALID_MESSAGE_UPDATED_PAYLOAD
        )
        assert isinstance(received, MessageCreatedPayload)
        assert received.metadata is not None
        assert received.metadata.delivery_status == {
            "agent-123": {
                "status": "processed",
                "delivered_at": "2025-11-17T11:20:11.000000Z",
                "processed_at": "2025-11-17T11:20:13.000000Z",
                "attempts": [{"attempt_number": 1, "status": "success"}],
            }
        }

    async def test_accepts_valid_room_added_payload(self):
        """Should accept valid room_added payload without raising."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client,
            "room_added",
            {
                "id": "room-123",
                "title": "Test Room",
                "task_id": None,
                "inserted_at": "2025-11-17T09:05:35.642172Z",
                "updated_at": "2025-11-17T09:05:35.642172Z",
            },
        )
        assert isinstance(received, RoomAddedPayload)
        assert received.id == "room-123"

    async def test_accepts_valid_room_removed_payload(self):
        """Should accept valid room_removed payload without raising."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client,
            "room_removed",
            {
                "id": "room-123",
                "title": "Test Room",
                "task_id": "task-1",
                "inserted_at": "2025-11-17T09:05:35Z",
                "updated_at": "2025-11-17T11:26:59Z",
            },
        )
        assert isinstance(received, RoomRemovedPayload)
        assert received.id == "room-123"

    async def test_accepts_minimal_room_removed_payload(self):
        """Should accept room_removed with only its required fields (title/task_id optional)."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client,
            "room_removed",
            {
                "id": "room-456",
                "inserted_at": "2025-11-17T09:05:35Z",
                "updated_at": "2025-11-17T09:05:35Z",
            },
        )
        assert isinstance(received, RoomRemovedPayload)
        assert received.id == "room-456"
        assert received.title is None
        assert received.task_id is None

    async def test_accepts_minimal_room_deleted_payload(self):
        """Should accept room_deleted with only required `id` field."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(client, "room_deleted", {"id": "room-789"})
        assert isinstance(received, RoomDeletedPayload)
        assert received.id == "room-789"

    async def test_accepts_valid_participant_added_payload(self):
        """Should accept valid participant_added payload and pass typed model to callback."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client,
            "participant_added",
            {
                "id": "p-123",
                "name": "Test Agent",
                "type": "Agent",
                "is_remote": True,
                "is_external": True,
            },
        )
        assert isinstance(received, ParticipantAddedPayload)
        assert received.id == "p-123"
        assert received.name == "Test Agent"
        assert received.is_remote is True
        assert received.is_external is True

    async def test_accepts_valid_participant_removed_payload(self):
        """Should accept valid participant_removed payload and pass typed model to callback."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client,
            "participant_removed",
            {"id": "p-123", "name": "Test Agent", "type": "Agent"},
        )
        assert isinstance(received, ParticipantRemovedPayload)
        assert received.id == "p-123"

    @pytest.mark.parametrize(
        ("event_name", "base_payload", "expected_type"),
        [
            pytest.param(
                "message_created",
                {
                    "id": "msg-123",
                    "content": "hi",
                    "message_type": "text",
                    "metadata": {
                        "mentions": [{"id": "a-1", "handle": "bot", "name": "Bot"}],
                        "status": "sent",
                    },
                    "sender_id": "u-1",
                    "sender_type": "User",
                    "chat_room_id": "r-1",
                    "thread_id": None,
                    "inserted_at": "2025-11-17T11:20:10Z",
                    "updated_at": "2025-11-17T11:20:10Z",
                },
                MessageCreatedPayload,
                id="message_created",
            ),
            pytest.param(
                "room_added",
                {
                    "id": "room-123",
                    "owner": {"id": "u-1", "name": "User", "type": "User"},
                    "status": "active",
                    "type": "direct",
                    "title": "Room",
                    "inserted_at": "2025-11-17T09:05:35Z",
                    "updated_at": "2025-11-17T09:05:35Z",
                    "participant_role": "member",
                },
                RoomAddedPayload,
                id="room_added",
            ),
            pytest.param(
                "room_removed",
                {
                    "id": "room-123",
                    "title": "Room",
                    "inserted_at": "2025-11-17T09:05:35Z",
                    "updated_at": "2025-11-17T11:26:59Z",
                },
                RoomRemovedPayload,
                id="room_removed",
            ),
            pytest.param(
                "room_deleted",
                {"id": "room-123"},
                RoomDeletedPayload,
                id="room_deleted",
            ),
            pytest.param(
                "participant_added",
                {"id": "p-123", "name": "Agent", "type": "Agent"},
                ParticipantAddedPayload,
                id="participant_added",
            ),
            pytest.param(
                "participant_removed",
                {"id": "p-123", "name": "Agent", "type": "Agent"},
                ParticipantRemovedPayload,
                id="participant_removed",
            ),
        ],
    )
    async def test_allows_extra_fields_in_payload(
        self, event_name, base_payload, expected_type
    ):
        """Should accept payloads with extra fields (forward compatibility)."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        extra_fields = {"extra_field_1": "some value", "extra_field_2": 42}
        received = await dispatch(client, event_name, {**base_payload, **extra_fields})
        assert isinstance(received, expected_type)

    async def test_validation_error_count_increments_on_invalid_payload(self, caplog):
        """Should increment validation_error_count when a payload fails validation."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        assert client.validation_error_count == 0

        invalid_payload = {"id": "msg-123"}  # Missing required fields
        with caplog.at_level(logging.ERROR):
            await dispatch(client, "message_created", invalid_payload)

        assert client.validation_error_count == 1

        # Send another invalid payload to verify it keeps incrementing
        with caplog.at_level(logging.ERROR):
            await dispatch(client, "message_created", invalid_payload)

        assert client.validation_error_count == 2

    async def test_validation_error_count_stays_zero_on_valid_payload(self):
        """Should not increment validation_error_count for valid payloads."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        await dispatch(client, "message_created", VALID_MESSAGE_CREATED_PAYLOAD)
        assert client.validation_error_count == 0

    async def test_hydration_shape_mismatch_is_dropped_and_counted_not_raised(
        self, monkeypatch, caplog
    ):
        """A payload band-sdk-core accepts but whose shape can't be hydrated must be
        dropped and counted like any other invalid event, never escape the seam.

        Fault-injects a shape band-sdk-core's own rules don't happen to catch, to
        exercise the seam's `except (TypeError, AttributeError)` branch: `_hydrate`
        raises `TypeError` walking a non-dict mention item, which escapes
        `_handle_events` uncaught without that branch.
        """

        def fake_validate(event_type, raw, trace_context=None):
            return {**raw, "metadata": {"mentions": [1]}}

        monkeypatch.setattr(
            wire_module.band_sdk_core, "validate_event_payload", fake_validate
        )

        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        with caplog.at_level(logging.ERROR):
            received = await dispatch(
                client, WireEvent.MESSAGE_CREATED, VALID_MESSAGE_CREATED_PAYLOAD
            )

        assert received is None
        assert client.validation_error_count == 1
        assert "message_created payload passed band-sdk-core but failed to hydrate" in (
            caplog.text
        )

    async def test_reset_validation_error_count_returns_previous_value(self):
        """Should reset validation_error_count back to zero and return old value."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        # Drive the counter up
        await dispatch(client, "message_created", {"id": "msg-123"})  # Missing fields
        assert client.validation_error_count == 1

        old_count = client.reset_validation_error_count()
        assert old_count == 1
        assert client.validation_error_count == 0
