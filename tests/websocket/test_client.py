"""
Tests for WebSocket payload validation.

These tests ensure the SDK handles invalid payloads gracefully by logging
errors and skipping malformed events, rather than crashing the connection.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from phoenix_channels_python_client.exceptions import PHXConnectionError
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from band.client.streaming import (
    MessageCreatedPayload,
    SupersedePayload,
    WebSocketDisconnectReason,
    WebSocketUpgradeError,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    WebSocketClient,
)

# Shared valid payload used by multiple tests
VALID_MESSAGE_CREATED_PAYLOAD: dict = {
    "id": "msg-123",
    "content": "@TestBot hi",
    "message_type": "text",
    "metadata": {
        "mentions": [{"id": "agent-123", "handle": "testbot", "name": "TestBot"}],
        "status": "sent",
    },
    "sender_id": "user-456",
    "sender_type": "User",
    "chat_room_id": "room-123",
    "thread_id": None,
    "inserted_at": "2025-11-17T11:20:10.284136Z",
    "updated_at": "2025-11-17T11:20:10.284136Z",
}


def _upgrade_exception(
    status_code: int, body: bytes, headers: dict[str, str] | None = None
):
    return InvalidStatus(
        Response(
            status_code=status_code,
            reason_phrase="error",
            headers=Headers(headers or {}),
            body=body,
        )
    )


# --- Invalid payload tests: verify graceful handling (log + skip) ---


async def test_skips_invalid_message_created_payload(caplog):
    """Should log error and skip when message_created payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "message_created"
        payload = {
            "id": "msg-123",
            # Missing: content, sender_id, sender_type, etc.
        }

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"message_created": dummy_callback})

    assert not callback_called, "Callback should not be called for invalid payload"
    assert "Invalid message_created payload" in caplog.text


async def test_skips_invalid_room_added_payload(caplog):
    """Should log error and skip when room_added payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "room_added"
        payload = {
            # Missing required fields: id, inserted_at, updated_at
            "title": "Test Room",
        }

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"room_added": dummy_callback})

    assert not callback_called, "Callback should not be called for invalid payload"
    assert "Invalid room_added payload" in caplog.text


async def test_rejects_room_added_missing_timestamps(caplog):
    """Regression test for INT-186: room_added without inserted_at/updated_at must be rejected."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "room_added"
        payload = {
            "id": "room-123",
            "title": "Test Room",
            # Missing required: inserted_at, updated_at
        }

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"room_added": dummy_callback})

    assert not callback_called, "Callback should not be called without timestamps"
    assert "Invalid room_added payload" in caplog.text


async def test_skips_invalid_room_removed_payload(caplog):
    """Should log error and skip when room_removed payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "room_removed"
        payload = {
            # Missing required field: id
            "status": "closed",
        }

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"room_removed": dummy_callback})

    assert not callback_called, "Callback should not be called for invalid payload"
    assert "Invalid room_removed payload" in caplog.text


async def test_skips_invalid_room_deleted_payload(caplog):
    """Should log error and skip when room_deleted payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "room_deleted"
        payload = {}

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"room_deleted": dummy_callback})

    assert not callback_called, "Callback should not be called for invalid payload"
    assert "Invalid room_deleted payload" in caplog.text


async def test_skips_invalid_participant_added_payload(caplog):
    """Should log error and skip when participant_added payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "participant_added"
        payload = {
            "id": "p-123",
            # Missing required fields: name, type (only id is provided)
        }

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(
            MockMessage(), {"participant_added": dummy_callback}
        )

    assert not callback_called, "Callback should not be called for invalid payload"
    assert "Invalid participant_added payload" in caplog.text


async def test_skips_invalid_participant_removed_payload(caplog):
    """Should log error and skip when participant_removed payload is missing required fields."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    callback_called = False

    class MockMessage:
        event = "participant_removed"
        payload = {
            # Missing: id
        }

    async def dummy_callback(payload):
        nonlocal callback_called
        callback_called = True

    with caplog.at_level(logging.ERROR):
        await client._handle_events(
            MockMessage(), {"participant_removed": dummy_callback}
        )

    assert not callback_called, "Callback should not be called for invalid payload"
    assert "Invalid participant_removed payload" in caplog.text


async def test_supersede_event_records_terminal_reason_and_disables_reconnect():
    """agent_control supersede should record the server reason and stop reconnects."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    client.client = type("MockPhoenix", (), {"auto_reconnect": True})()
    received_payload = None

    async def on_supersede(payload: SupersedePayload):
        nonlocal received_payload
        received_payload = payload
        client.record_terminal_disconnect(payload.to_disconnect_reason())

    class MockMessage:
        event = "supersede"
        payload = {
            "reason": "session.already_connected",
            "message": "This connection has been superseded by a newer session for this agent.",
            "retryable": False,
            "retry_after": 15,
            "target_socket_id": "agent_socket:agent-123",
            "correlation_id": "evict-123",
        }

    await client._handle_events(MockMessage(), {"supersede": on_supersede})

    assert isinstance(received_payload, SupersedePayload)
    assert client.client.auto_reconnect is False
    assert client.last_disconnect_reason == WebSocketDisconnectReason(
        reason="session.already_connected",
        message="This connection has been superseded by a newer session for this agent.",
        retryable=False,
        retry_after=15,
        target_socket_id="agent_socket:agent-123",
        correlation_id="evict-123",
    )


def test_parses_distinct_upgrade_errors_from_http_json_response():
    cases = [
        (
            409,
            b'{"error":{"code":"connection_conflict","message":"already connected","request_id":"req-409"}}',
            "connection_conflict",
            None,
        ),
        (
            400,
            b'{"error":{"code":"invalid_on_conflict","message":"bad on_conflict","request_id":"req-400"}}',
            "invalid_on_conflict",
            None,
        ),
        (
            503,
            b'{"error":{"code":"tracking_failed","message":"tracking unavailable","request_id":"req-503"}}',
            "tracking_failed",
            None,
        ),
        (
            429,
            b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-429","retry_after":12}}',
            "too_many_requests",
            12,
        ),
    ]

    for status_code, body, code, retry_after in cases:
        err = WebSocketUpgradeError.from_exception(
            _upgrade_exception(status_code, body, {"Retry-After": "30"})
        )

        assert err is not None
        assert err.status_code == status_code
        assert err.code == code
        assert err.request_id == f"req-{status_code}"
        assert err.retry_after == retry_after


def test_uses_retry_after_header_for_429_upgrade_error():
    err = WebSocketUpgradeError.from_exception(
        _upgrade_exception(
            429,
            b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-header"}}',
            {"Retry-After": "30"},
        )
    )

    assert err is not None
    assert err.retry_after == 30


def test_ignores_generic_auth_upgrade_error_without_json_contract():
    err = WebSocketUpgradeError.from_exception(_upgrade_exception(403, b""))

    assert err is None


async def test_aenter_wraps_upgrade_error(monkeypatch):
    upgrade_exc = _upgrade_exception(
        409,
        b'{"error":{"code":"connection_conflict","message":"already connected","request_id":"req-409"}}',
    )

    class FailingPHXClient:
        def __init__(self, *args, **kwargs):
            self.channel_socket_url = "wss://test/socket"

        async def __aenter__(self):
            raise upgrade_exc

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", FailingPHXClient
    )

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with pytest.raises(WebSocketUpgradeError) as exc_info:
        await client.__aenter__()

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "connection_conflict"
    assert exc_info.value.request_id == "req-409"


async def test_aenter_probes_initial_phx_connection_error(monkeypatch):
    upgrade_exc = _upgrade_exception(
        429,
        b'{"error":{"code":"too_many_requests","message":"slow down","request_id":"req-429"}}',
        {"Retry-After": "30"},
    )
    probed_urls = []

    class FailingPHXClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["auto_reconnect"] is False
            self.channel_socket_url = "wss://test/socket"

        async def __aenter__(self):
            raise PHXConnectionError("Connection supervisor stopped before connecting")

    class FailingProbe:
        async def __aenter__(self):
            raise upgrade_exc

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    def fake_connect(url, *, open_timeout):
        probed_urls.append((url, open_timeout))
        return FailingProbe()

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", FailingPHXClient
    )
    monkeypatch.setattr("band.client.streaming.errors.connect", fake_connect)

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with pytest.raises(WebSocketUpgradeError) as exc_info:
        await client.__aenter__()

    assert probed_urls == [("wss://test/socket&agent_id=agent-123", 5)]
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "too_many_requests"
    assert exc_info.value.retry_after == 30


async def test_aenter_restores_reconnect_after_successful_initial_connect(monkeypatch):
    init_kwargs = {}

    class SuccessfulPHXClient:
        def __init__(self, *args, **kwargs):
            init_kwargs.update(kwargs)
            self.channel_socket_url = "wss://test/socket"
            self.auto_reconnect = kwargs["auto_reconnect"]

        async def __aenter__(self):
            return self

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", SuccessfulPHXClient
    )

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    await client.__aenter__()

    assert init_kwargs["auto_reconnect"] is False
    assert client.client.auto_reconnect is True


async def test_aenter_retries_unclassified_initial_connection_errors(monkeypatch):
    attempts = 0
    sleep_delays = []

    class FlakyPHXClient:
        def __init__(self, *args, **kwargs):
            self.channel_socket_url = "wss://test/socket"
            self.auto_reconnect = kwargs["auto_reconnect"]

        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PHXConnectionError("temporary network failure")
            return self

    async def no_upgrade_error(exc, websocket_url):
        return None

    async def fake_sleep(delay):
        sleep_delays.append(delay)

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", FlakyPHXClient
    )
    monkeypatch.setattr(
        "band.client.streaming.client.classify_initial_upgrade_error",
        no_upgrade_error,
    )
    monkeypatch.setattr("band.client.streaming.client.asyncio.sleep", fake_sleep)

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    await client.__aenter__()

    assert attempts == 3
    assert len(sleep_delays) == 2
    assert client.client.auto_reconnect is True


async def test_aenter_reraises_unrecognized_upgrade_error(monkeypatch):
    original_exc = RuntimeError("socket exploded")

    class FailingPHXClient:
        def __init__(self, *args, **kwargs):
            self.channel_socket_url = "wss://test/socket"

        async def __aenter__(self):
            raise original_exc

    monkeypatch.setattr(
        "band.client.streaming.client.PHXChannelsClient", FailingPHXClient
    )

    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    with pytest.raises(RuntimeError, match="socket exploded"):
        await client.__aenter__()


# --- Valid payload tests ---


async def test_accepts_valid_message_created_payload():
    """Should accept valid message_created payload without raising."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    await client._handle_events(MockMessage(), {"message_created": test_callback})
    assert isinstance(received_payload, MessageCreatedPayload)
    assert received_payload.id == "msg-123"


async def test_accepts_valid_room_added_payload():
    """Should accept valid room_added payload without raising."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "room_added"
        payload = {
            "id": "room-123",
            "title": "Test Room",
            "task_id": None,
            "inserted_at": "2025-11-17T09:05:35.642172Z",
            "updated_at": "2025-11-17T09:05:35.642172Z",
        }

    await client._handle_events(MockMessage(), {"room_added": test_callback})
    assert isinstance(received_payload, RoomAddedPayload)
    assert received_payload.id == "room-123"


async def test_accepts_valid_room_removed_payload():
    """Should accept valid room_removed payload without raising."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "room_removed"
        payload = {
            "id": "room-123",
            "status": "active",
            "type": "direct",
            "title": "Test Room",
            "removed_at": "2025-11-17T11:26:59.925707",
        }

    await client._handle_events(MockMessage(), {"room_removed": test_callback})
    assert isinstance(received_payload, RoomRemovedPayload)
    assert received_payload.id == "room-123"


async def test_accepts_minimal_room_removed_payload():
    """Should accept room_removed with only required `id` field (all others optional)."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "room_removed"
        payload = {"id": "room-456"}

    await client._handle_events(MockMessage(), {"room_removed": test_callback})
    assert isinstance(received_payload, RoomRemovedPayload)
    assert received_payload.id == "room-456"
    assert received_payload.status is None
    assert received_payload.type is None
    assert received_payload.title is None
    assert received_payload.removed_at is None


async def test_accepts_minimal_room_deleted_payload():
    """Should accept room_deleted with only required `id` field."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "room_deleted"
        payload = {"id": "room-789"}

    await client._handle_events(MockMessage(), {"room_deleted": test_callback})
    assert isinstance(received_payload, RoomDeletedPayload)
    assert received_payload.id == "room-789"


async def test_accepts_valid_participant_added_payload():
    """Should accept valid participant_added payload and pass typed model to callback."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "participant_added"
        payload = {
            "id": "p-123",
            "name": "Test Agent",
            "type": "Agent",
        }

    await client._handle_events(MockMessage(), {"participant_added": test_callback})
    assert isinstance(received_payload, ParticipantAddedPayload)
    assert received_payload.id == "p-123"
    assert received_payload.name == "Test Agent"


async def test_accepts_valid_participant_removed_payload():
    """Should accept valid participant_removed payload and pass typed model to callback."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "participant_removed"
        payload = {
            "id": "p-123",
        }

    await client._handle_events(MockMessage(), {"participant_removed": test_callback})
    assert isinstance(received_payload, ParticipantRemovedPayload)
    assert received_payload.id == "p-123"


async def test_join_room_participants_channel_allows_omitted_room_deleted_handler():
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    client.client = AsyncMock()

    async def on_participant_added(payload):
        pass

    async def on_participant_removed(payload):
        pass

    await client.join_room_participants_channel(
        "room-123",
        on_participant_added=on_participant_added,
        on_participant_removed=on_participant_removed,
    )

    client.client.subscribe_to_topic.assert_awaited_once()
    topic, message_handler = client.client.subscribe_to_topic.await_args.args
    assert topic == "room_participants:room-123"

    class MockMessage:
        event = "room_deleted"
        payload = {"id": "room-123"}

    await message_handler(MockMessage())


async def test_join_room_participants_channel_routes_room_deleted_handler():
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    client.client = AsyncMock()
    received_payload = None

    async def on_participant_added(payload):
        pass

    async def on_participant_removed(payload):
        pass

    async def on_room_deleted(payload):
        nonlocal received_payload
        received_payload = payload

    await client.join_room_participants_channel(
        "room-123",
        on_participant_added=on_participant_added,
        on_participant_removed=on_participant_removed,
        on_room_deleted=on_room_deleted,
    )

    _, message_handler = client.client.subscribe_to_topic.await_args.args

    class MockMessage:
        event = "room_deleted"
        payload = {"id": "room-123"}

    await message_handler(MockMessage())

    assert isinstance(received_payload, RoomDeletedPayload)
    assert received_payload.id == "room-123"


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
                "status": "active",
                "type": "direct",
                "title": "Room",
                "removed_at": "2025-11-17T11:26:59Z",
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
            {"id": "p-123"},
            ParticipantRemovedPayload,
            id="participant_removed",
        ),
    ],
)
async def test_allows_extra_fields_in_payload(event_name, base_payload, expected_type):
    """Should accept payloads with extra fields (forward compatibility)."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    extra_fields = {"extra_field_1": "some value", "extra_field_2": 42}

    class MockMessage:
        event = event_name
        payload = {**base_payload, **extra_fields}

    await client._handle_events(MockMessage(), {event_name: test_callback})
    assert isinstance(received_payload, expected_type)


async def test_skips_unknown_event_without_handler(caplog):
    """Should warn when receiving an event with no registered handler."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "unknown_event"
        payload = {"data": "test"}

    with caplog.at_level(logging.WARNING):
        await client._handle_events(MockMessage(), {})

    assert "no handler registered" in caplog.text


async def test_passes_raw_dict_for_unknown_event_types():
    """Should pass raw payload dict for event types without Pydantic models."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    received_payload = None

    async def test_callback(payload):
        nonlocal received_payload
        received_payload = payload

    class MockMessage:
        event = "task_created"
        payload = {"task_id": "t-123", "status": "pending"}

    await client._handle_events(MockMessage(), {"task_created": test_callback})
    assert received_payload == {"task_id": "t-123", "status": "pending"}


# --- Validation error counter tests ---


async def test_validation_error_count_increments_on_invalid_payload(caplog):
    """Should increment validation_error_count when a payload fails validation."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")
    assert client.validation_error_count == 0

    class MockMessage:
        event = "message_created"
        payload = {"id": "msg-123"}  # Missing required fields

    async def dummy_callback(payload):
        pass

    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"message_created": dummy_callback})

    assert client.validation_error_count == 1

    # Send another invalid payload to verify it keeps incrementing
    with caplog.at_level(logging.ERROR):
        await client._handle_events(MockMessage(), {"message_created": dummy_callback})

    assert client.validation_error_count == 2


async def test_validation_error_count_stays_zero_on_valid_payload():
    """Should not increment validation_error_count for valid payloads."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    async def dummy_callback(payload):
        pass

    await client._handle_events(MockMessage(), {"message_created": dummy_callback})
    assert client.validation_error_count == 0


async def test_reset_validation_error_count_returns_previous_value():
    """Should reset validation_error_count back to zero and return old value."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "message_created"
        payload = {"id": "msg-123"}  # Missing required fields

    async def dummy_callback(payload):
        pass

    # Drive the counter up
    await client._handle_events(MockMessage(), {"message_created": dummy_callback})
    assert client.validation_error_count == 1

    old_count = client.reset_validation_error_count()
    assert old_count == 1
    assert client.validation_error_count == 0


async def test_callback_exception_does_not_crash_handler(caplog):
    """Should log exception and not propagate when callback raises."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    async def failing_callback(payload):
        raise RuntimeError("callback boom")

    with caplog.at_level(logging.ERROR):
        await client._handle_events(
            MockMessage(), {"message_created": failing_callback}
        )

    assert "Callback error for message_created event" in caplog.text
    assert client.validation_error_count == 0


async def test_cancelled_error_propagates_through_callback():
    """CancelledError raised in callback must propagate (not be swallowed)."""
    client = WebSocketClient("ws://localhost", "test-key", "agent-123")

    class MockMessage:
        event = "message_created"
        payload = VALID_MESSAGE_CREATED_PAYLOAD

    async def cancelling_callback(payload):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await client._handle_events(
            MockMessage(), {"message_created": cancelling_callback}
        )
