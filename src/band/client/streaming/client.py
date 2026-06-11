from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import random

from phoenix_channels_python_client.client import (
    PHXChannelsClient,
    PhoenixChannelsProtocolVersion,
)
from phoenix_channels_python_client.client_types import ReconnectPolicy
from phoenix_channels_python_client.exceptions import PHXConnectionError
from phoenix_channels_python_client.phx_messages import PHXMessage
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from band.client.streaming.errors import classify_initial_upgrade_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebSocketDisconnectReason:
    """Terminal WebSocket disconnect reason reported by the platform."""

    reason: str
    message: str
    retryable: bool
    retry_after: int | None = None
    target_socket_id: str | None = None
    correlation_id: str | None = None


# WebSocket message payloads (based on actual backend messages)
# Using Pydantic for runtime validation


class Mention(BaseModel):
    """Mention object within message metadata."""

    model_config = ConfigDict(extra="allow")

    id: str
    username: str | None = None
    handle: str | None = None
    name: str | None = None


class MessageMetadata(BaseModel):
    """Metadata within message_created payload."""

    model_config = ConfigDict(extra="allow")

    mentions: list[Mention] = []
    status: str | None = None


class MessageCreatedPayload(BaseModel):
    """Payload for message_created events (observed from real WebSocket)."""

    model_config = ConfigDict(
        extra="allow"
    )  # Allow extra fields backend might add later

    id: str
    content: str
    message_type: str
    metadata: MessageMetadata | None = None
    sender_id: str
    sender_type: str
    sender_name: str | None = None
    chat_room_id: str | None = None
    thread_id: str | None = None
    inserted_at: str
    updated_at: str


class RoomAddedPayload(BaseModel):
    """Payload for room_added events.

    Required/optional fields aligned with the Fern-generated ChatRoom model
    (thenvoi_rest.types.chat_room.ChatRoom). The WebSocket may include
    additional fields which are captured by ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    inserted_at: str
    updated_at: str
    title: str | None = None
    task_id: str | None = None


class RoomRemovedPayload(BaseModel):
    """Payload for room_removed events.

    WebSocket-only event with no Fern-generated model; all fields except
    ``id`` are kept optional as a defensive default.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    status: str | None = None
    type: str | None = None
    title: str | None = None
    removed_at: str | None = None


class RoomDeletedPayload(BaseModel):
    """Payload for room_deleted events on room_participants channels."""

    model_config = ConfigDict(extra="allow")

    id: str


async def _noop_room_deleted(_: RoomDeletedPayload) -> None:
    return None


class ParticipantAddedPayload(BaseModel):
    """Payload for participant_added events."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    type: str
    is_remote: bool | None = None
    is_external: bool | None = None  # Legacy alias for is_remote

    @model_validator(mode="after")
    def _sync_remote_aliases(self) -> "ParticipantAddedPayload":
        if self.is_remote is None and self.is_external is not None:
            self.is_remote = self.is_external
        if self.is_external is None and self.is_remote is not None:
            self.is_external = self.is_remote
        return self


class ParticipantRemovedPayload(BaseModel):
    """Payload for participant_removed events."""

    model_config = ConfigDict(extra="allow")

    id: str


# Contact event payloads


class ContactRequestReceivedPayload(BaseModel):
    """Payload for contact_request_received events."""

    model_config = ConfigDict(extra="allow")

    id: str
    from_handle: str
    from_name: str
    message: str | None = None
    status: str
    inserted_at: str


class ContactRequestUpdatedPayload(BaseModel):
    """Payload for contact_request_updated events."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str


class ContactAddedPayload(BaseModel):
    """Payload for contact_added events."""

    model_config = ConfigDict(extra="allow")

    id: str
    handle: str
    name: str
    type: str
    description: str | None = None
    is_remote: bool | None = None
    is_external: bool | None = None  # Legacy alias for is_remote
    inserted_at: str

    @model_validator(mode="after")
    def _sync_remote_aliases(self) -> "ContactAddedPayload":
        if self.is_remote is None and self.is_external is not None:
            self.is_remote = self.is_external
        if self.is_external is None and self.is_remote is not None:
            self.is_external = self.is_remote
        return self


class ContactRemovedPayload(BaseModel):
    """Payload for contact_removed events."""

    model_config = ConfigDict(extra="allow")

    id: str


class SupersedePayload(BaseModel):
    """Payload for terminal agent_control supersede events."""

    model_config = ConfigDict(extra="allow")

    reason: str
    message: str
    retryable: bool
    retry_after: int | None = None
    target_socket_id: str | None = None
    correlation_id: str | None = None

    def to_disconnect_reason(self) -> WebSocketDisconnectReason:
        return WebSocketDisconnectReason(
            reason=self.reason,
            message=self.message,
            retryable=self.retryable,
            retry_after=self.retry_after,
            target_socket_id=self.target_socket_id,
            correlation_id=self.correlation_id,
        )


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "message_created": MessageCreatedPayload,
    "room_added": RoomAddedPayload,
    "room_removed": RoomRemovedPayload,
    "room_deleted": RoomDeletedPayload,
    "participant_added": ParticipantAddedPayload,
    "participant_removed": ParticipantRemovedPayload,
    "contact_request_received": ContactRequestReceivedPayload,
    "contact_request_updated": ContactRequestUpdatedPayload,
    "contact_added": ContactAddedPayload,
    "contact_removed": ContactRemovedPayload,
    "supersede": SupersedePayload,
}


def _initial_reconnect_delay(policy: ReconnectPolicy, attempt: int) -> float:
    delay = min(
        policy.max_delay_s, policy.base_delay_s * (policy.factor ** max(attempt, 0))
    )
    if delay <= 0:
        return 0.0
    return (delay / 2) + (random.random() * (delay / 2))


class WebSocketClient:
    def __init__(
        self,
        ws_url: str,
        api_key: str,
        agent_id: str | None = None,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
        on_disconnect: Callable[[Exception | None], Awaitable[None]] | None = None,
    ):
        self.ws_url = ws_url
        self.api_key = api_key
        self.agent_id = agent_id
        self.client: PHXChannelsClient | None = None
        self._on_reconnect = on_reconnect
        self._on_disconnect = on_disconnect
        self._validation_error_count: int = 0
        self._last_disconnect_reason: WebSocketDisconnectReason | None = None

    @property
    def validation_error_count(self) -> int:
        """Number of events dropped due to payload validation errors."""
        return self._validation_error_count

    @property
    def last_disconnect_reason(self) -> WebSocketDisconnectReason | None:
        """Most recent terminal disconnect reason reported by the platform."""
        return self._last_disconnect_reason

    def reset_validation_error_count(self) -> int:
        """Reset the validation error counter and return the previous value.

        Useful for periodic metric flushes (non-atomic, safe for single event loop).
        """
        count = self._validation_error_count
        self._validation_error_count = 0
        return count

    def _require_client(self) -> PHXChannelsClient:
        if self.client is None:
            raise RuntimeError("WebSocket client is not connected")
        return self.client

    async def __aenter__(self):
        """Create and enter the PHXChannelsClient context"""
        policy = ReconnectPolicy()
        for attempt in range(policy.rapid_suppress_disconnect_count + 1):
            self.client = PHXChannelsClient(
                self.ws_url,
                self.api_key,
                protocol_version=PhoenixChannelsProtocolVersion.V2,
                auto_reconnect=False,
                on_reconnect=self._on_reconnect,
                on_disconnect=self._on_disconnect,
            )
            if self.agent_id:
                self.client.channel_socket_url += f"&agent_id={self.agent_id}"
            try:
                await self.client.__aenter__()
            except Exception as exc:
                upgrade_error = await classify_initial_upgrade_error(
                    exc, self.client.channel_socket_url
                )
                if upgrade_error is not None:
                    raise upgrade_error from exc
                if not isinstance(exc, PHXConnectionError):
                    raise
                if attempt >= policy.rapid_suppress_disconnect_count:
                    raise
                delay = _initial_reconnect_delay(policy, attempt)
                logger.warning(
                    "Initial WebSocket connection failed; retrying in %.2fs: %s",
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            else:
                self.client.auto_reconnect = True
                return self

        raise RuntimeError("WebSocket client failed to connect")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the PHXChannelsClient context"""
        if self.client:
            await self.client.__aexit__(exc_type, exc_val, exc_tb)

    async def _handle_events(self, message: PHXMessage, event_handlers: dict):
        """Generic async event handler that maps events to their corresponding async callbacks"""
        logger.debug("[WebSocket] Received event: %s", message.event)

        # Check if we have a handler for this event
        if message.event not in event_handlers:
            logger.warning(
                "[WebSocket] Received event '%s' but no handler registered. "
                "Available handlers: %s",
                message.event,
                list(event_handlers.keys()),
            )
            return

        # Validate and parse payload into Pydantic models for known types
        model = _PAYLOAD_MODELS.get(message.event)
        if model is not None:
            try:
                validated = model(**message.payload)
            except ValidationError as e:
                errors = "; ".join(
                    f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                logger.error(
                    "[WebSocket] Invalid %s payload: %s",
                    message.event,
                    errors,
                )
                logger.debug(
                    "[WebSocket] Raw payload for invalid %s: %s",
                    message.event,
                    message.payload,
                )
                self._validation_error_count += 1
                return
        else:
            # Unknown event types: pass the raw payload dict
            validated = message.payload

        callback = event_handlers[message.event]
        if callback:
            try:
                await callback(validated)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 – intentionally broad to protect event loop
                logger.exception(
                    "[WebSocket] Callback error for %s event", message.event
                )

    def record_terminal_disconnect(self, reason: WebSocketDisconnectReason) -> None:
        """Record a terminal platform disconnect reason and disable reconnect."""
        self._last_disconnect_reason = reason
        self.disable_reconnect()

    def disable_reconnect(self) -> None:
        """Disable PHX auto-reconnect for a terminal platform disconnect."""
        if self.client:
            self.client.auto_reconnect = False

    async def join_agent_control_channel(
        self,
        agent_id: str,
        on_supersede: Callable[[SupersedePayload], Awaitable[None]],
    ):
        """Subscribe to terminal agent-control events for this agent."""
        topic = f"agent_control:{agent_id}"
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(message, {"supersede": on_supersede})

        result = await self._require_client().subscribe_to_topic(topic, message_handler)
        logger.info("[WebSocket] Subscribed to topic: %s", topic)
        return result

    async def join_agent_rooms_channel(
        self,
        agent_id: str,
        on_room_added: Callable[[RoomAddedPayload], Awaitable[None]],
        on_room_removed: Callable[[RoomRemovedPayload], Awaitable[None]],
    ):
        """Subscribe to agent rooms topic with async callbacks"""
        topic = f"agent_rooms:{agent_id}"
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(
                message, {"room_added": on_room_added, "room_removed": on_room_removed}
            )

        result = await self._require_client().subscribe_to_topic(topic, message_handler)
        logger.info("[WebSocket] Subscribed to topic: %s", topic)
        return result

    async def join_chat_room_channel(
        self,
        chat_room_id: str,
        on_message_created: Callable[[MessageCreatedPayload], Awaitable[None]],
    ):
        """Subscribe to chat room topic for message events with async callback"""
        topic = f"chat_room:{chat_room_id}"
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(message, {"message_created": on_message_created})

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def join_user_rooms_channel(
        self,
        user_id: str,
        on_room_added: Callable[[RoomAddedPayload], Awaitable[None]],
        on_room_removed: Callable[[RoomRemovedPayload], Awaitable[None]],
    ):
        """Subscribe to user rooms topic with async callbacks"""
        topic = f"user_rooms:{user_id}"

        async def message_handler(message):
            await self._handle_events(
                message, {"room_added": on_room_added, "room_removed": on_room_removed}
            )

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def join_room_participants_channel(
        self,
        chat_room_id: str,
        on_participant_added: Callable[[ParticipantAddedPayload], Awaitable[None]],
        on_participant_removed: Callable[[ParticipantRemovedPayload], Awaitable[None]],
        on_room_deleted: Callable[
            [RoomDeletedPayload], Awaitable[None]
        ] = _noop_room_deleted,
    ):
        """Subscribe to room participants topic with async callbacks"""
        topic = f"room_participants:{chat_room_id}"
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    "participant_added": on_participant_added,
                    "participant_removed": on_participant_removed,
                    "room_deleted": on_room_deleted,
                },
            )

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def join_tasks_channel(
        self,
        user_id: str,
        on_task_created: Callable[[dict], Awaitable[None]],
        on_task_updated: Callable[[dict], Awaitable[None]],
    ):
        """Subscribe to tasks topic with async callbacks"""
        topic = f"tasks:{user_id}"

        async def message_handler(message):
            await self._handle_events(
                message,
                {"task_created": on_task_created, "task_updated": on_task_updated},
            )

        return await self._require_client().subscribe_to_topic(topic, message_handler)

    async def leave_agent_control_channel(self, agent_id: str):
        """Unsubscribe from agent control topic"""
        topic = f"agent_control:{agent_id}"
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_agent_rooms_channel(self, agent_id: str):
        """Unsubscribe from agent rooms topic"""
        topic = f"agent_rooms:{agent_id}"
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_chat_room_channel(self, chat_room_id: str):
        """Unsubscribe from chat room topic"""
        topic = f"chat_room:{chat_room_id}"
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_user_rooms_channel(self, user_id: str):
        """Unsubscribe from user rooms topic"""
        topic = f"user_rooms:{user_id}"
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_room_participants_channel(self, chat_room_id: str):
        """Unsubscribe from room participants topic"""
        topic = f"room_participants:{chat_room_id}"
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def leave_tasks_channel(self, user_id: str):
        """Unsubscribe from tasks topic"""
        topic = f"tasks:{user_id}"
        return await self._require_client().unsubscribe_from_topic(topic)

    async def join_agent_contacts_channel(
        self,
        agent_id: str,
        on_contact_request_received: Callable[
            [ContactRequestReceivedPayload], Awaitable[None]
        ],
        on_contact_request_updated: Callable[
            [ContactRequestUpdatedPayload], Awaitable[None]
        ],
        on_contact_added: Callable[[ContactAddedPayload], Awaitable[None]],
        on_contact_removed: Callable[[ContactRemovedPayload], Awaitable[None]],
    ):
        """Subscribe to agent contacts topic with async callbacks."""
        topic = f"agent_contacts:{agent_id}"
        logger.info("[WebSocket] Subscribing to topic: %s", topic)

        async def message_handler(message):
            await self._handle_events(
                message,
                {
                    "contact_request_received": on_contact_request_received,
                    "contact_request_updated": on_contact_request_updated,
                    "contact_added": on_contact_added,
                    "contact_removed": on_contact_removed,
                },
            )

        result = await self._require_client().subscribe_to_topic(topic, message_handler)
        logger.info("[WebSocket] Subscribed to topic: %s", topic)
        return result

    async def leave_agent_contacts_channel(self, agent_id: str):
        """Unsubscribe from agent contacts topic."""
        topic = f"agent_contacts:{agent_id}"
        logger.info("[WebSocket] Unsubscribing from topic: %s", topic)
        return await self._require_client().unsubscribe_from_topic(topic)

    async def run_forever(self):
        await self._require_client().run_forever()
