"""
RoomPresence - Cross-room lifecycle management.

Extracted from ThenvoiAgent room lifecycle methods.
Handles agent's presence across rooms. Does NOT handle what happens inside rooms.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Set

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.platform.event import (
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
    ReconnectedEvent,
    PlatformEvent,
    ContactEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
)
from thenvoi.platform.link import ThenvoiLink

# Type alias for contact event callback (agent-level, no tools)
ContactEventHandler = Callable[[ContactEvent], Awaitable[None]]

logger = logging.getLogger(__name__)


class RoomPresence:
    """
    Manages agent's presence across rooms.

    Cross-room only. Does NOT handle what happens inside rooms.
    That's the job of Execution implementations.

    Extracted from ThenvoiAgent room lifecycle:
    - _on_room_added() -> on_room_joined callback
    - _on_room_removed() -> on_room_left callback
    - _subscribe_to_existing_rooms() -> start() auto-subscription

    Example:
        import logging
        logger = logging.getLogger(__name__)

        link = ThenvoiLink(agent_id, api_key, ...)
        presence = RoomPresence(link)

        async def on_joined(room_id: str, payload: dict):
            logger.info("Joined room %s", room_id)

        async def on_event(room_id: str, event: PlatformEvent):
            if isinstance(event, MessageEvent):
                logger.info("Message in %s: %s", room_id, event.payload.content)

        presence.on_room_joined = on_joined
        presence.on_room_event = on_event

        await presence.start()
        # Presence now consumes events via async iterator internally
        await link.run_forever()
    """

    def __init__(
        self,
        link: ThenvoiLink,
        room_filter: Callable[[dict], bool] | None = None,
        auto_subscribe_existing: bool = True,
    ):
        """
        Initialize RoomPresence.

        Args:
            link: ThenvoiLink for WebSocket events
            room_filter: Optional filter to decide which rooms to join
            auto_subscribe_existing: Subscribe to existing rooms on start
        """
        self.link = link
        self.room_filter = room_filter
        self.auto_subscribe_existing = auto_subscribe_existing

        # Track rooms we're present in
        self.rooms: Set[str] = set()

        # Callbacks (set by user or AgentRuntime)
        self.on_room_joined: Callable[[str, dict], Awaitable[None]] | None = None
        self.on_room_left: Callable[[str], Awaitable[None]] | None = None
        self.on_room_event: Callable[[str, PlatformEvent], Awaitable[None]] | None = (
            None
        )
        self.on_contact_event: ContactEventHandler | None = None
        self.on_reconnected: Callable[[], Awaitable[None]] | None = None

        # Internal task for consuming events from link
        self._event_task: asyncio.Task | None = None

    async def start(self) -> None:
        """
        Start presence management.

        1. Connect link if not connected
        2. Subscribe to agent room events
        3. Subscribe to existing rooms (if configured)
        4. Spawn task to consume events from link
        """
        # Connect if needed
        if not self.link.is_connected:
            await self.link.connect()

        # Subscribe to room added/removed events
        await self.link.subscribe_agent_rooms(self.link.agent_id)

        # Subscribe to existing rooms
        if self.auto_subscribe_existing:
            await self._subscribe_to_existing_rooms()

        # Spawn task to consume events from link's async iterator
        self._event_task = asyncio.create_task(self._consume_events())

        logger.info("RoomPresence started for agent %s", self.link.agent_id)

    async def _consume_events(self) -> None:
        """Consume events from link's async iterator."""
        try:
            async for event in self.link:
                await self._on_platform_event(event)
        except asyncio.CancelledError:
            logger.debug("Event consumer task cancelled")
        except Exception as e:
            logger.error("Error in event consumer: %s", e, exc_info=True)

    async def stop(self) -> None:
        """
        Stop presence management.

        Cancels event consumer, unsubscribes from all rooms and clears state.
        Does NOT disconnect the link (caller may want to reuse it).
        """
        # Cancel event consumer task
        if self._event_task and not self._event_task.done():
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        # Notify left for all rooms
        for room_id in list(self.rooms):
            if self.on_room_left:
                try:
                    await self.on_room_left(room_id)
                except Exception as e:
                    logger.warning("on_room_left error for %s: %s", room_id, e)

        self.rooms.clear()
        logger.info("RoomPresence stopped")

    async def _on_platform_event(self, event: PlatformEvent) -> None:
        """
        Handle platform events from ThenvoiLink.

        Routes to appropriate handler based on event type.
        """
        match event:
            case RoomAddedEvent():
                await self._handle_room_added(event)
            case RoomRemovedEvent() | RoomDeletedEvent():
                await self._handle_room_left(event)
            case ReconnectedEvent():
                await self._handle_reconnect()
            case (
                ContactRequestReceivedEvent()
                | ContactRequestUpdatedEvent()
                | ContactAddedEvent()
                | ContactRemovedEvent()
            ):
                # Contact events have no room_id - forward to contact handler
                await self._handle_contact_event(event)
            case _ if event.room_id:
                # Room-specific event - forward to on_room_event
                await self._handle_room_event(event)

    async def _handle_room_added(self, event: RoomAddedEvent) -> None:
        """
        Handle room_added event.

        Extracted from ThenvoiAgent._on_room_added().
        """
        room_id = event.room_id
        if not room_id or not event.payload:
            logger.warning("room_added event without room_id or payload")
            return

        payload = event.payload.model_dump(exclude_none=True)

        # Apply filter if configured
        if self.room_filter and not self.room_filter(payload):
            logger.debug("Room %s filtered out", room_id)
            return

        # Track room
        self.rooms.add(room_id)

        # Subscribe to room channels
        await self.link.subscribe_room(room_id)

        # Notify callback
        if self.on_room_joined:
            try:
                await self.on_room_joined(room_id, payload)
            except Exception as e:
                logger.error(
                    "on_room_joined error for %s: %s", room_id, e, exc_info=True
                )

        logger.info("Agent joined room: %s", room_id)

    async def _handle_room_removed(self, event: RoomRemovedEvent) -> None:
        """Handle room_removed event."""
        await self._handle_room_left(event)

    async def _handle_room_left(
        self, event: RoomRemovedEvent | RoomDeletedEvent
    ) -> None:
        """
        Handle room_removed and room_deleted events.

        Both events mean the room should be torn down locally.
        """
        room_id = event.room_id
        if not room_id:
            logger.warning("%s event without room_id", event.type)
            return

        # Unsubscribe from room channels
        await self.link.unsubscribe_room(room_id)

        # Untrack room
        self.rooms.discard(room_id)

        # Notify callback
        if self.on_room_left:
            try:
                await self.on_room_left(room_id)
            except Exception as e:
                logger.error("on_room_left error for %s: %s", room_id, e, exc_info=True)

        logger.info("Agent left room via %s: %s", event.type, room_id)

    async def _handle_reconnect(self) -> None:
        """
        Reconcile tracked rooms with the server after WebSocket reconnection.

        PHXChannelsClient already re-subscribes previously joined room topics.
        This method therefore syncs local room state against the API instead of
        replaying room joins, unsubscribing rooms that disappeared while the
        socket was down and only subscribing rooms that are newly discovered.

        on_reconnected is always fired at the end so callers can trigger a
        /next resync for already-active rooms even if room reconciliation hit a
        transient API failure.
        """
        logger.info("Handling reconnection — syncing rooms from API")
        old_rooms = self.rooms.copy()

        try:
            try:
                rooms_from_api = await self._list_existing_rooms()
            except Exception as e:
                logger.warning("Failed to sync rooms after reconnect: %s", e)
                return

            current_room_ids = {room_id for room_id, _ in rooms_from_api}
            self.rooms = old_rooms & current_room_ids

            gone_rooms = old_rooms - current_room_ids
            for room_id in gone_rooms:
                try:
                    await self.link.unsubscribe_room(room_id)
                except Exception as e:
                    logger.warning(
                        "Failed to unsubscribe room %s during reconnect: %s",
                        room_id,
                        e,
                    )
                self.rooms.discard(room_id)
                if self.on_room_left:
                    try:
                        await self.on_room_left(room_id)
                    except Exception as e:
                        logger.warning(
                            "on_room_left error for %s during reconnect: %s",
                            room_id,
                            e,
                        )

            surviving_room_ids = sorted(self.rooms)

            if self.auto_subscribe_existing:
                new_rooms = [
                    (room_id, payload)
                    for room_id, payload in rooms_from_api
                    if room_id not in old_rooms
                ]
                await self._subscribe_rooms(new_rooms, context="reconnect")

            if self.on_room_event:
                reconnect_event = ReconnectedEvent()
                for room_id in surviving_room_ids:
                    try:
                        await self.on_room_event(room_id, reconnect_event)
                    except Exception as e:
                        logger.warning(
                            "on_room_event error for %s during reconnect: %s",
                            room_id,
                            e,
                        )

        finally:
            # Notify callers so they can resync /next for messages missed during downtime
            if self.on_reconnected:
                try:
                    await self.on_reconnected()
                except asyncio.CancelledError:
                    # Preserve structured concurrency: never swallow cancellation.
                    raise
                except Exception as e:
                    logger.warning("on_reconnected callback error: %s", e)

    async def _handle_room_event(self, event: PlatformEvent) -> None:
        """
        Handle room-specific events (message, participant changes).

        Forwards to on_room_event callback.
        """
        room_id = event.room_id
        if not room_id:
            return

        # Only forward events for rooms we're tracking
        if room_id not in self.rooms:
            logger.debug("Event for untracked room %s, ignoring", room_id)
            return

        if self.on_room_event:
            try:
                await self.on_room_event(room_id, event)
            except Exception as e:
                logger.error(
                    "on_room_event error for %s: %s", room_id, e, exc_info=True
                )

    async def _handle_contact_event(self, event: ContactEvent) -> None:
        """
        Handle contact events (requests, added, removed).

        Contact events have no room context and are agent-level.
        Forwards to on_contact_event callback.
        """
        if self.on_contact_event:
            try:
                await self.on_contact_event(event)
            except Exception as e:
                logger.error(
                    "on_contact_event error for %s: %s",
                    type(event).__name__,
                    e,
                    exc_info=True,
                )

    async def _list_existing_rooms(self) -> list[tuple[str, dict[str, Any]]]:
        """Fetch all current rooms from the API, applying the room filter."""
        all_rooms = []
        page = 1
        page_size = 100
        while True:
            response = await self.link.rest.agent_api_chats.list_agent_chats(
                page=page,
                page_size=page_size,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            if response.data:
                all_rooms.extend(response.data)

            total_pages = getattr(response.metadata, "total_pages", None)
            if total_pages is None or page >= total_pages:
                break
            page += 1

        rooms: list[tuple[str, dict[str, Any]]] = []
        for room in all_rooms:
            payload = room.model_dump(exclude_none=True)
            if self.room_filter and not self.room_filter(payload):
                continue
            rooms.append((room.id, payload))

        return rooms

    async def _subscribe_rooms(
        self,
        rooms_to_join: list[tuple[str, dict[str, Any]]],
        *,
        context: str,
    ) -> None:
        """Subscribe to rooms in parallel and report aggregate success/failure."""
        if not rooms_to_join:
            return

        async def safe_subscribe(room_id: str, payload: dict[str, Any]) -> bool:
            """Subscribe to a single room, returning True on success."""
            try:
                await self.link.subscribe_room(room_id)
                self.rooms.add(room_id)

                if self.on_room_joined:
                    await self.on_room_joined(room_id, payload)
                return True
            except Exception as e:
                logger.warning(
                    "Failed to subscribe to room %s during %s: %s",
                    room_id,
                    context,
                    e,
                )
                self.rooms.discard(room_id)
                return False

        results = await asyncio.gather(
            *[safe_subscribe(room_id, payload) for room_id, payload in rooms_to_join],
        )
        succeeded = sum(1 for result in results if result)
        failed = len(results) - succeeded

        if failed:
            logger.warning(
                "Subscribed to %s rooms during %s (%s failed)",
                succeeded,
                context,
                failed,
            )
        else:
            logger.info("Subscribed to %s rooms during %s", succeeded, context)

    async def _subscribe_to_existing_rooms(self) -> None:
        """
        Subscribe to all rooms where agent is a participant.

        Fetches room list from the API (paginated) and joins channels in
        parallel. Each room join is isolated so one failure doesn't affect
        others.
        """
        logger.debug("Subscribing to existing rooms")

        try:
            rooms_to_join = await self._list_existing_rooms()
            await self._subscribe_rooms(rooms_to_join, context="startup")
        except Exception as e:
            logger.warning("Failed to subscribe to existing rooms: %s", e)
