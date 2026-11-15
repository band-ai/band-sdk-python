"""WebSocketClient routes each wire event to its registered handler."""

from __future__ import annotations

import asyncio
import logging

import pytest
from phoenix_channels_python_client.messages import UserMessage

from band.client.streaming import (
    MessageCreatedPayload,
    RoomDeletedPayload,
    WebSocketClient,
)
from band.client.streaming.client import WireEvent
from band.testing import fake_phoenix_server
from tests.websocket.client.kit import (
    VALID_MESSAGE_CREATED_PAYLOAD,
    VALID_MESSAGE_UPDATED_PAYLOAD,
    dispatch,
)


class TestEventRouting:
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

        await client._handle_events(
            UserMessage(
                topic="chat_room:room-123",
                ref=None,
                event=WireEvent.MESSAGE_CREATED,
                payload=VALID_MESSAGE_CREATED_PAYLOAD,
            ),
            handlers,
        )
        await client._handle_events(
            UserMessage(
                topic="chat_room:room-123",
                ref=None,
                event=WireEvent.MESSAGE_UPDATED,
                payload=VALID_MESSAGE_UPDATED_PAYLOAD,
            ),
            handlers,
        )

        assert created_calls == ["msg-123"]
        assert updated_calls == ["msg-123"]

    async def test_ignores_message_updated_when_no_handler_registered(self, caplog):
        """Back-compat: a message_updated frame is dropped (not an error) when only
        message_created is wired up, as before the optional handler was added."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")
        created_calls: list[str] = []

        async def on_created(payload):
            created_calls.append(payload.id)

        with caplog.at_level(logging.WARNING):
            await client._handle_events(
                UserMessage(
                    topic="chat_room:room-123",
                    ref=None,
                    event=WireEvent.MESSAGE_UPDATED,
                    payload=VALID_MESSAGE_UPDATED_PAYLOAD,
                ),
                {WireEvent.MESSAGE_CREATED: on_created},
            )

        assert created_calls == []  # not misrouted to the message_created handler

    async def test_join_room_participants_channel_allows_omitted_room_deleted_handler(
        self,
    ) -> None:
        received = asyncio.Queue()
        async with (
            fake_phoenix_server() as server,
            WebSocketClient(server.url, "test-key", "agent-123") as client,
        ):
            await client.join_room_participants_channel(
                "room-123",
                on_participant_added=received.put,
                on_participant_removed=received.put,
            )
            await server.push(
                "room_participants:room-123", WireEvent.ROOM_DELETED, {"id": "room-123"}
            )
            await server.push(
                "room_participants:room-123",
                WireEvent.PARTICIPANT_ADDED,
                {"id": "agent-456", "name": "Peer", "type": "Agent"},
            )
            assert (await received.get()).id == "agent-456"

    async def test_join_room_participants_channel_routes_room_deleted_handler(
        self,
    ) -> None:
        received: asyncio.Queue[RoomDeletedPayload] = asyncio.Queue()
        participants = asyncio.Queue()
        async with (
            fake_phoenix_server() as server,
            WebSocketClient(server.url, "test-key", "agent-123") as client,
        ):
            await client.join_room_participants_channel(
                "room-123",
                on_participant_added=participants.put,
                on_participant_removed=participants.put,
                on_room_deleted=received.put,
            )
            await server.push(
                "room_participants:room-123", WireEvent.ROOM_DELETED, {"id": "room-123"}
            )
            assert (await received.get()).model_dump() == {"id": "room-123"}

    async def test_skips_unknown_event_without_handler(self, caplog):
        """Should warn when receiving an event with no registered handler."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.WARNING):
            await client._handle_events(
                UserMessage(
                    topic="room:1",
                    ref=None,
                    event="unknown_event",
                    payload={"data": "test"},
                ),
                {},
            )

        assert "no handler registered" in caplog.text

    async def test_event_created_without_handler_logs_at_debug_not_warning(
        self, caplog
    ):
        """event_created has no handler/PlatformEvent anywhere yet (event rows are read
        back over REST instead) -- this is expected, so it must not warn like a
        genuinely unregistered event does, but it should still be visible at debug."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        with caplog.at_level(logging.DEBUG, logger="band.client.streaming.client"):
            await client._handle_events(
                UserMessage(
                    topic="room:1",
                    ref=None,
                    event=WireEvent.EVENT_CREATED,
                    payload={"data": "test"},
                ),
                {},
            )

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

        async def failing_callback(payload):
            raise RuntimeError("callback boom")

        with caplog.at_level(logging.ERROR):
            await client._handle_events(
                UserMessage(
                    topic="chat_room:room-123",
                    ref=None,
                    event=WireEvent.MESSAGE_CREATED,
                    payload=VALID_MESSAGE_CREATED_PAYLOAD,
                ),
                {WireEvent.MESSAGE_CREATED: failing_callback},
            )

        assert "Callback error for message_created event" in caplog.text
        assert client.validation_error_count == 0

    async def test_cancelled_error_propagates_through_callback(self):
        """CancelledError raised in callback must propagate (not be swallowed)."""
        client = WebSocketClient("ws://localhost", "test-key", "agent-123")

        async def cancelling_callback(payload):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await client._handle_events(
                UserMessage(
                    topic="chat_room:room-123",
                    ref=None,
                    event=WireEvent.MESSAGE_CREATED,
                    payload=VALID_MESSAGE_CREATED_PAYLOAD,
                ),
                {WireEvent.MESSAGE_CREATED: cancelling_callback},
            )

    async def test_binary_platform_payload_is_dropped_before_valid_delivery(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = WebSocketClient("ws://peer.invalid", "test-key")
        received: asyncio.Queue[MessageCreatedPayload] = asyncio.Queue()
        handlers = {WireEvent.MESSAGE_CREATED: received.put}
        secret = b"synthetic-secret-binary-payload"
        caplog.set_level(logging.DEBUG, logger="band.client.streaming.client")
        await client._handle_events(
            UserMessage(
                topic="room:1",
                ref=None,
                event=WireEvent.MESSAGE_CREATED,
                payload=secret,
            ),
            handlers,
        )
        await client._handle_events(
            UserMessage(
                topic="room:1",
                ref=None,
                event=WireEvent.MESSAGE_CREATED,
                payload=VALID_MESSAGE_CREATED_PAYLOAD,
            ),
            handlers,
        )
        assert client.validation_error_count == 1
        assert secret.decode() not in caplog.text
        assert "Dropping binary payload" in caplog.text
        message = await received.get()
        assert (message.id, message.content) == (
            VALID_MESSAGE_CREATED_PAYLOAD["id"],
            VALID_MESSAGE_CREATED_PAYLOAD["content"],
        )

    async def test_binary_raw_payload_is_dropped_before_valid_delivery(self) -> None:
        client = WebSocketClient("ws://peer.invalid", "test-key")
        received: asyncio.Queue[object] = asyncio.Queue()
        handlers = {WireEvent.TASK_CREATED: received.put}
        await client._handle_events(
            UserMessage(
                topic="tasks:1",
                ref=None,
                event=WireEvent.TASK_CREATED,
                payload=b"unsupported",
            ),
            handlers,
        )
        payload = {"task_id": "task-1", "status": "pending"}
        await client._handle_events(
            UserMessage(
                topic="tasks:1", ref=None, event=WireEvent.TASK_CREATED, payload=payload
            ),
            handlers,
        )
        assert await received.get() == payload
        assert client.validation_error_count == 1
