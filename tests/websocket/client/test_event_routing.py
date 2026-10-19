"""WebSocketClient routes each wire event to its registered handler."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from opentelemetry.sdk.trace import TracerProvider

from band.client.streaming import RoomDeletedPayload, WebSocketClient
from tests.websocket.client.kit import (
    VALID_MESSAGE_CREATED_PAYLOAD,
    VALID_MESSAGE_UPDATED_PAYLOAD,
    dispatch,
)


class TestEventRouting:
    async def test_trace_context_round_trips_through_band_sdk_core_to_the_log(
        self, caplog
    ):
        """A real OpenTelemetry span's traceparent reaches the real band_sdk_core
        call, comes back on the raised ValueError, and lands on the seam's own
        log record -- through the actual OTel propagation and band_sdk_core
        binding, neither faked. Uses a standalone TracerProvider instance (never
        ``trace.set_tracer_provider``), so this doesn't touch the process-global
        provider other tests may depend on.
        """
        tracer = TracerProvider().get_tracer("test")
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with (
            caplog.at_level(logging.ERROR),
            tracer.start_as_current_span("probe") as span,
        ):
            span_context = span.get_span_context()
            # Missing required fields -- a genuine band_sdk_core rejection.
            received = await dispatch(client, "message_created", {"id": "msg-123"})

        assert received is None
        record = next(
            r for r in caplog.records if "Invalid message_created" in r.getMessage()
        )
        assert record.trace_context is not None
        assert format(span_context.trace_id, "032x") in record.trace_context
        assert format(span_context.span_id, "016x") in record.trace_context

    async def test_routes_message_created_and_updated_to_distinct_handlers(self):
        """When both handlers are registered, each event reaches only its own one."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        created_calls: list[str] = []
        updated_calls: list[str] = []

        async def on_created(payload):
            created_calls.append(payload.id)

        async def on_updated(payload):
            updated_calls.append(payload.id)

        handlers = {"message_created": on_created, "message_updated": on_updated}

        class CreatedMsg:
            event = "message_created"
            payload = VALID_MESSAGE_CREATED_PAYLOAD

        class UpdatedMsg:
            event = "message_updated"
            payload = VALID_MESSAGE_UPDATED_PAYLOAD

        await client._handle_events(CreatedMsg(), handlers)
        await client._handle_events(UpdatedMsg(), handlers)

        assert created_calls == ["msg-123"]
        assert updated_calls == ["msg-123"]

    async def test_ignores_message_updated_when_no_handler_registered(self, caplog):
        """Back-compat: a message_updated frame is dropped (not an error) when only
        message_created is wired up, as before the optional handler was added."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        created_calls: list[str] = []

        async def on_created(payload):
            created_calls.append(payload.id)

        class UpdatedMsg:
            event = "message_updated"
            payload = VALID_MESSAGE_UPDATED_PAYLOAD

        with caplog.at_level(logging.WARNING):
            await client._handle_events(UpdatedMsg(), {"message_created": on_created})

        assert created_calls == []  # not misrouted to the message_created handler

    async def test_join_room_participants_channel_allows_omitted_room_deleted_handler(
        self,
    ):
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
            payload: ClassVar[dict[str, Any]] = {"id": "room-123"}

        await message_handler(MockMessage())

    async def test_join_room_participants_channel_routes_room_deleted_handler(self):
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
            payload: ClassVar[dict[str, Any]] = {"id": "room-123"}

        await message_handler(MockMessage())

        assert isinstance(received_payload, RoomDeletedPayload)
        assert received_payload.id == "room-123"

    async def test_skips_unknown_event_without_handler(self, caplog):
        """Should warn when receiving an event with no registered handler."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        class MockMessage:
            event = "unknown_event"
            payload: ClassVar[dict[str, Any]] = {"data": "test"}

        with caplog.at_level(logging.WARNING):
            await client._handle_events(MockMessage(), {})

        assert "no handler registered" in caplog.text

    async def test_event_created_without_handler_logs_at_debug_not_warning(
        self, caplog
    ):
        """event_created has no handler/PlatformEvent anywhere yet (event rows are read
        back over REST instead) -- this is expected, so it must not warn like a
        genuinely unregistered event does, but it should still be visible at debug."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        class MockMessage:
            event = "event_created"
            payload: ClassVar[dict[str, Any]] = {"data": "test"}

        with caplog.at_level(logging.DEBUG, logger="band.client.streaming.client"):
            await client._handle_events(MockMessage(), {})

        unhandled = [r for r in caplog.records if "no handler registered" in r.message]
        assert len(unhandled) == 1
        assert unhandled[0].levelno == logging.DEBUG

    async def test_passes_raw_dict_for_unknown_event_types(self):
        """Should pass raw payload dict for event types without Pydantic models."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        received = await dispatch(
            client, "task_created", {"task_id": "t-123", "status": "pending"}
        )
        assert received == {"task_id": "t-123", "status": "pending"}

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
