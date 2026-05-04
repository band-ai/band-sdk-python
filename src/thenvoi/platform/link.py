"""
ThenvoiLink - Live link to Thenvoi platform.

Extracted from core/agent.py ThenvoiAgent - WebSocket management only.
REST client exposed directly for API calls.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from thenvoi.client.rest import AsyncRestClient, DEFAULT_REQUEST_OPTIONS
from thenvoi.client.streaming import WebSocketClient
from thenvoi.runtime.types import PlatformMessage
from thenvoi_rest.core.api_error import ApiError

from .event import (
    MessageEvent,
    RoomAddedEvent,
    RoomRemovedEvent,
    RoomDeletedEvent,
    ReconnectedEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
    PlatformEvent,
)

if TYPE_CHECKING:
    from thenvoi.client.streaming import (
        MessageCreatedPayload,
        ParticipantAddedPayload,
        ParticipantRemovedPayload,
        RoomAddedPayload,
        RoomDeletedPayload,
        RoomRemovedPayload,
        ContactRequestReceivedPayload,
        ContactRequestUpdatedPayload,
        ContactAddedPayload,
        ContactRemovedPayload,
    )

logger = logging.getLogger(__name__)


class ThenvoiLink:
    """
    Live link to Thenvoi platform.

    Extracted from ThenvoiAgent - handles WebSocket connection and event dispatch.
    REST client exposed directly via self.rest for API calls.

    Example:
        import logging
        logger = logging.getLogger(__name__)

        link = ThenvoiLink(agent_id="...", api_key="...")
        await link.connect()
        await link.subscribe_agent_rooms(agent_id)

        async for event in link:
            match event:
                case MessageEvent(payload=msg):
                    logger.info("Message: %s", msg.content)
                case RoomAddedEvent(room_id=rid):
                    await link.subscribe_room(rid)
    """

    def __init__(
        self,
        agent_id: str,
        api_key: str,
        ws_url: str = "wss://app.thenvoi.com/api/v1/socket/websocket",
        rest_url: str = "https://app.thenvoi.com",
    ):
        self.agent_id = agent_id
        self.api_key = api_key
        self.ws_url = ws_url
        self.rest_url = rest_url

        # REST client - exposed directly (from ThenvoiAgent._api_client)
        self.rest = AsyncRestClient(api_key=api_key, base_url=rest_url)

        # WebSocket client (from ThenvoiAgent._ws_client)
        self._ws: WebSocketClient | None = None
        self._is_connected = False

        # Subscription tracking (from ThenvoiAgent._subscribed_rooms)
        self._subscribed_rooms: set[str] = set()

        # Event queue for async iteration
        self._event_queue: asyncio.Queue[PlatformEvent] = asyncio.Queue(maxsize=1000)

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    # --- Async iterator protocol ---

    def __aiter__(self):
        """Return self to allow async iteration over events."""
        return self

    async def __anext__(self) -> PlatformEvent:
        """Get next event from the queue. Blocks until an event is available."""
        return await self._event_queue.get()

    # --- Connection lifecycle (from ThenvoiAgent.start/stop/run) ---

    async def connect(self) -> None:
        """
        Connect WebSocket.

        Extracted from ThenvoiAgent.start() lines 158-164.
        """
        if self._is_connected:
            logger.warning("Already connected")
            return

        self._ws = WebSocketClient(
            self.ws_url,
            self.api_key,
            self.agent_id,
            on_reconnect=self._on_reconnected,
            on_disconnect=self._on_disconnected,
        )
        await self._ws.__aenter__()
        self._is_connected = True
        logger.info("Connected to platform")

    async def disconnect(self) -> None:
        """
        Disconnect WebSocket.

        Extracted from ThenvoiAgent.stop() lines 193-195.
        """
        if not self._is_connected or not self._ws:
            return

        await self._ws.__aexit__(None, None, None)
        self._ws = None
        self._is_connected = False
        self._subscribed_rooms.clear()
        logger.info("Disconnected from platform")

    async def run_forever(self) -> None:
        """
        Run until interrupted.

        From ThenvoiAgent.run() lines 208-209.
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.run_forever()

    # --- Subscription management (from ThenvoiAgent) ---

    async def subscribe_agent_rooms(self, agent_id: str) -> None:
        """
        Subscribe to agent room events (room_added/removed).

        From ThenvoiAgent.start() lines 167-171.
        """
        if not self._ws:
            raise RuntimeError("Not connected")

        await self._ws.join_agent_rooms_channel(
            agent_id,
            on_room_added=self._on_room_added,
            on_room_removed=self._on_room_removed,
        )

    async def subscribe_room(self, room_id: str) -> None:
        """
        Subscribe to room messages and participants.

        Extracted from ThenvoiAgent._subscribe_to_room() lines 724-746.
        Wraps each channel join so a single room failure doesn't crash
        the entire subscription sequence.
        """
        if not self._ws:
            raise RuntimeError("Not connected")

        if room_id in self._subscribed_rooms:
            return

        try:
            # Subscribe to messages (from lines 733-736)
            await self._ws.join_chat_room_channel(
                room_id,
                on_message_created=lambda msg: self._on_message_created(room_id, msg),
            )
        except Exception as e:
            logger.warning("Failed to join chat_room:%s: %s", room_id, e)
            return

        try:
            # Subscribe to participant updates (from lines 739-743)
            await self._ws.join_room_participants_channel(
                room_id,
                on_participant_added=lambda p: self._on_participant_added(room_id, p),
                on_participant_removed=lambda p: self._on_participant_removed(
                    room_id, p
                ),
                on_room_deleted=lambda p: self._on_room_deleted(room_id, p),
            )
        except Exception as e:
            logger.warning("Failed to join room_participants:%s: %s", room_id, e)
            # Clean up the chat_room channel we already joined
            try:
                await self._ws.leave_chat_room_channel(room_id)
            except Exception:
                pass
            return

        self._subscribed_rooms.add(room_id)
        logger.debug("Subscribed to room %s", room_id)

    async def subscribe_agent_contacts(self, agent_id: str) -> None:
        """
        Subscribe to agent contact events.

        Events: contact_request_received, contact_request_updated,
                contact_added, contact_removed
        """
        if not self._ws:
            raise RuntimeError("Not connected")

        await self._ws.join_agent_contacts_channel(
            agent_id,
            on_contact_request_received=self._on_contact_request_received,
            on_contact_request_updated=self._on_contact_request_updated,
            on_contact_added=self._on_contact_added,
            on_contact_removed=self._on_contact_removed,
        )

    async def unsubscribe_room(self, room_id: str) -> None:
        """
        Unsubscribe from room.

        Extracted from ThenvoiAgent._unsubscribe_from_room() lines 748-769.
        """
        if not self._ws or room_id not in self._subscribed_rooms:
            return

        self._subscribed_rooms.discard(room_id)

        try:
            await self._ws.leave_chat_room_channel(room_id)
        except Exception as e:
            logger.warning("Error unsubscribing from chat_room:%s: %s", room_id, e)

        try:
            await self._ws.leave_room_participants_channel(room_id)
        except Exception as e:
            logger.warning(
                "Error unsubscribing from room_participants:%s: %s", room_id, e
            )

        logger.debug("Unsubscribed from room %s", room_id)

    async def unsubscribe_agent_contacts(self) -> None:
        """Unsubscribe from agent contacts channel."""
        if not self._ws:
            return
        try:
            await self._ws.leave_agent_contacts_channel(self.agent_id)
        except Exception as e:
            logger.warning("Error unsubscribing from agent_contacts: %s", e)

    # --- Event handlers (from ThenvoiAgent, unified into PlatformEvent) ---

    async def _on_reconnected(self) -> None:
        """Handle PHX client reconnection.

        PHXChannelsClient re-subscribes previously joined topics before calling
        this hook, so room subscription tracking must stay intact here.
        RoomPresence can then reconcile tracked rooms against the server state
        without leaking channels or replaying duplicate room joins.
        """
        logger.info("WebSocket reconnected — reconciling room state")
        self._queue_event(ReconnectedEvent())

    async def _on_disconnected(self, error: Exception | None) -> None:
        """Handle PHX client disconnection."""
        logger.warning("WebSocket disconnected: %s", error)

    def _queue_event(self, event: PlatformEvent) -> None:
        """Queue event for async iteration. Logs warning if queue is full."""
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Event queue full, dropping %s event for room %s",
                event.type,
                event.room_id,
            )

    def queue_event(self, event: PlatformEvent) -> None:
        """Queue a synthetic event for processing (public API)."""
        self._queue_event(event)

    async def _on_room_added(self, payload: "RoomAddedPayload") -> None:
        """
        Handle room_added from WebSocket.

        From ThenvoiAgent._on_room_added() lines 619-630.
        Now creates RoomAddedEvent and queues it for async iteration.
        """
        event = RoomAddedEvent(
            room_id=payload.id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_room_removed(self, payload: "RoomRemovedPayload") -> None:
        """
        Handle room_removed from WebSocket.

        From ThenvoiAgent._on_room_removed() lines 632-643.
        """
        event = RoomRemovedEvent(
            room_id=payload.id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_message_created(
        self, room_id: str, payload: "MessageCreatedPayload"
    ) -> None:
        """
        Handle message_created from WebSocket.

        From ThenvoiAgent._on_message_created() lines 645-682.
        Now creates MessageEvent and queues it for async iteration.
        """
        event = MessageEvent(
            room_id=room_id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_room_deleted(
        self, room_id: str, payload: "RoomDeletedPayload"
    ) -> None:
        """
        Handle room_deleted from WebSocket.

        Room deletions arrive on room_participants:{room_id} with a minimal payload.
        """
        event = RoomDeletedEvent(
            room_id=room_id or payload.id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_participant_added(
        self, room_id: str, payload: "ParticipantAddedPayload"
    ) -> None:
        """
        Handle participant_added from WebSocket.

        From ThenvoiAgent._on_participant_added() lines 771-786.
        Payload is already validated by WebSocketClient._handle_events().
        """
        event = ParticipantAddedEvent(
            room_id=room_id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_participant_removed(
        self, room_id: str, payload: "ParticipantRemovedPayload"
    ) -> None:
        """
        Handle participant_removed from WebSocket.

        From ThenvoiAgent._on_participant_removed() lines 788-805.
        Payload is already validated by WebSocketClient._handle_events().
        """
        event = ParticipantRemovedEvent(
            room_id=room_id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_contact_request_received(
        self, payload: "ContactRequestReceivedPayload"
    ) -> None:
        """Handle contact_request_received from WebSocket."""
        logger.debug(
            "WebSocket: contact_request_received from %s (%s), request_id=%s",
            payload.from_name,
            payload.from_handle,
            payload.id,
        )
        event = ContactRequestReceivedEvent(
            room_id=None,  # Contact events have no room context
            payload=payload,
        )
        self._queue_event(event)

    async def _on_contact_request_updated(
        self, payload: "ContactRequestUpdatedPayload"
    ) -> None:
        """Handle contact_request_updated from WebSocket."""
        logger.debug(
            "WebSocket: contact_request_updated request_id=%s, status=%s",
            payload.id,
            payload.status,
        )
        event = ContactRequestUpdatedEvent(
            room_id=None,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_contact_added(self, payload: "ContactAddedPayload") -> None:
        """Handle contact_added from WebSocket."""
        logger.debug(
            "WebSocket: contact_added %s (%s), contact_id=%s",
            payload.name,
            payload.handle,
            payload.id,
        )
        event = ContactAddedEvent(
            room_id=None,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_contact_removed(self, payload: "ContactRemovedPayload") -> None:
        """Handle contact_removed from WebSocket."""
        logger.debug("WebSocket: contact_removed contact_id=%s", payload.id)
        event = ContactRemovedEvent(
            room_id=None,
            payload=payload,
        )
        self._queue_event(event)

    # --- Message lifecycle (SDK internal operations) ---

    async def mark_processing(self, room_id: str, message_id: str) -> None:
        """
        Mark message as being processed on the server.

        Tells the server this message is being handled, so /next won't return it.
        """
        logger.debug("Marking message %s as processing", message_id)
        try:
            await self.rest.agent_api_messages.mark_agent_message_processing(
                chat_id=room_id,
                id=message_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
            logger.warning("Failed to mark message %s as processing: %s", message_id, e)

    async def mark_processed(self, room_id: str, message_id: str) -> None:
        """
        Mark message as successfully processed on the server.

        Clears the message from unprocessed queue.
        """
        logger.debug("Marking message %s as processed", message_id)
        try:
            await self.rest.agent_api_messages.mark_agent_message_processed(
                chat_id=room_id,
                id=message_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
            logger.warning("Failed to mark message %s as processed: %s", message_id, e)

    async def mark_failed(self, room_id: str, message_id: str, error: str) -> None:
        """
        Mark message as failed on the server.

        Records the error and may trigger retry logic on the server side.
        """
        error = error.strip() or "Unknown error"
        logger.warning("Marking message %s as failed: %s", message_id, error)
        try:
            await self.rest.agent_api_messages.mark_agent_message_failed(
                chat_id=room_id,
                id=message_id,
                error=error,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
            logger.warning("Failed to mark message %s as failed: %s", message_id, e)

    async def get_next_message(self, room_id: str) -> PlatformMessage | None:
        """
        Get next unprocessed message for a room from the server.

        Used during sync to process backlog messages missed while offline.

        Returns:
            PlatformMessage if there's an unprocessed message,
            None if no unprocessed messages (204 No Content) or on error.
        """
        logger.debug("Getting next message for room %s", room_id)
        try:
            response = await self.rest.agent_api_messages.get_agent_next_message(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            if response.data is None:
                return None

            item = response.data
            return PlatformMessage(
                id=item.id,
                room_id=item.chat_room_id or room_id,
                content=item.content,
                sender_id=item.sender_id,
                sender_type=item.sender_type,
                sender_name=item.sender_name or "",
                message_type=item.message_type,
                metadata=item.metadata or {},
                created_at=item.inserted_at or datetime.now(timezone.utc),
            )
        except ApiError as e:
            # 204 No Content means no unprocessed messages - expected
            if e.status_code == 204:
                logger.debug("No unprocessed messages for room %s", room_id)
                return None
            logger.warning("Failed to get next message: %s", e)
            return None
        except Exception as e:
            logger.warning("Failed to get next message: %s", e)
            return None

    async def get_stale_processing_messages(
        self, room_id: str
    ) -> list[PlatformMessage]:
        """
        Get messages stuck in 'processing' state for a room.

        On agent restart, messages that were being processed when the agent
        crashed remain in 'processing' state. The /next endpoint skips them,
        so we need to find and re-process them explicitly.

        Returns:
            List of PlatformMessage objects in processing state.
        """
        try:
            messages = []
            page = 1
            while True:
                response = await self.rest.agent_api_messages.list_agent_messages(
                    chat_id=room_id,
                    status="processing",
                    page=page,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                for item in response.data:
                    messages.append(
                        PlatformMessage(
                            id=item.id,
                            room_id=item.chat_room_id or room_id,
                            content=item.content,
                            sender_id=item.sender_id,
                            sender_type=item.sender_type,
                            sender_name=item.sender_name or "",
                            message_type=item.message_type,
                            metadata=item.metadata or {},
                            created_at=item.inserted_at or datetime.now(timezone.utc),
                        )
                    )

                total_pages = response.metadata.total_pages
                if total_pages is None or page >= total_pages:
                    break
                page += 1

            return messages
        except Exception as e:
            logger.warning(
                "Failed to get stale processing messages for room %s: %s",
                room_id,
                e,
            )
            return []
