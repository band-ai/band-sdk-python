from __future__ import annotations

import asyncio
import logging

import pytest

import band.client.streaming.wire as wire_module
from band.client.streaming import (
    WebSocketClient,
    WireEvent,
)
from tests.websocket.clientkit import VALID_MESSAGE_CREATED_PAYLOAD, dispatch


class TestClientValidation:
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

    async def test_callback_exception_does_not_crash_handler(self, caplog):
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

    async def test_cancelled_error_propagates_through_callback(self):
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
