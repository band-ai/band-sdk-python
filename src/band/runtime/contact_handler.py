"""
ContactEventHandler - Handles contact WebSocket events.

Routes events based on ContactEventConfig strategy:
- DISABLED: Ignores all contact events
- CALLBACK: Calls programmatic callback
- HUB_ROOM: Routes to dedicated hub room for LLM reasoning

Handles at PlatformRuntime level (singleton) to avoid race conditions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from band.client.rest import (
    DEFAULT_REQUEST_OPTIONS,
    ChatEventRequest,
    ChatRoomRequest,
)
from band.client.streaming import MessageCreatedPayload, MessageMetadata
from band.platform.event import (
    ContactEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
    MessageEvent,
)
from band.runtime.contact_tools import ContactTools
from band.runtime.types import (
    ContactEventConfig,
    ContactEventStrategy,
    SYNTHETIC_SENDER_TYPE,
    SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
    SYNTHETIC_CONTACT_EVENTS_SENDER_NAME,
    normalize_handle,
)

if TYPE_CHECKING:
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)

# Type for hub room event injection callback
HubEventCallback = Callable[[str, MessageEvent], Awaitable[None]]

# Type for hub room initialization callback (injects system prompt)
HubInitCallback = Callable[[str, str], Awaitable[None]]

# Hub room system prompt - injected when hub room is first used
HUB_ROOM_SYSTEM_PROMPT = """## OVERRIDE: Contact Management Mode

This is your CONTACTS HUB - a dedicated room for managing contact requests.

**IMPORTANT: Do NOT delegate or add participants here.** You handle contact events DIRECTLY using the contact tools below. Do NOT call band_lookup_peers() or band_add_participant() in this room.

## Your Role

1. **Review incoming contact requests** - When you see a [Contact Request] message, evaluate it
2. **Take action** - Use the contact tools to respond:
   - `band_respond_contact_request(action="approve", request_id="...")` to accept
   - `band_respond_contact_request(action="reject", request_id="...")` to decline
3. **Report your decision** - Send a thought event explaining what you did

## Example

[Contact Events]: [Contact Request] Alice (@alice) wants to connect.
Request ID: abc-123

Your response:
1. band_send_event("Received contact request from Alice. Approving.", message_type="thought")
2. band_respond_contact_request(action="approve", request_id="abc-123")
3. band_send_event("Approved contact request from Alice (@alice)", message_type="thought")

## Contact Tools (use these, NOT participant tools)
- `band_respond_contact_request(action, request_id)` - Approve/reject requests
- `band_list_contact_requests()` - List pending requests
- `band_list_contacts()` - List current contacts
"""

# Maximum size of deduplication cache
MAX_DEDUP_CACHE_SIZE = 1000


class ContactEventHandler:
    """
    Handles contact WebSocket events based on strategy.

    Operates at agent level (not per-room) to avoid race conditions
    when agent is in multiple rooms simultaneously.

    Example:
        handler = ContactEventHandler(config, link, on_broadcast=broadcast_fn)
        await handler.handle(event)  # Routes based on strategy
    """

    def __init__(
        self,
        config: ContactEventConfig,
        link: "BandLink",
        on_broadcast: Callable[[str], None] | None = None,
        on_hub_event: HubEventCallback | None = None,
        on_hub_init: HubInitCallback | None = None,
    ):
        """
        Initialize ContactEventHandler.

        Args:
            config: Contact event configuration
            link: BandLink for REST API access
            on_broadcast: Optional callback to queue broadcast messages
            on_hub_event: Callback to inject events into hub room (for HUB_ROOM strategy)
            on_hub_init: Callback to inject system prompt when hub room is first used
        """
        self._config = config
        self._link = link
        self._on_broadcast = on_broadcast
        self._on_hub_event = on_hub_event
        self._on_hub_init = on_hub_init

        # ContactTools instance for callbacks (lazy initialized)
        self._contact_tools: ContactTools | None = None

        # Deduplication cache: event_id -> processed flag
        # Using OrderedDict to maintain insertion order for LRU eviction
        self._processed_events: OrderedDict[str, bool] = OrderedDict()

        # Hub room ID (for HUB_ROOM strategy, created at startup)
        self._hub_room_id: str | None = None

        # Whether hub room has been initialized with system prompt
        self._hub_room_initialized: bool = False

        # Event signaling hub room is ready (ExecutionContext exists)
        self._hub_room_ready: asyncio.Event = asyncio.Event()

        # Lock for thread-safe hub room creation
        self._hub_room_lock: asyncio.Lock = asyncio.Lock()

        # Cache for contact request info (request_id -> {from_handle, from_name, message})
        # Used to enrich ContactRequestUpdatedEvent with sender info
        self._request_cache: OrderedDict[str, dict[str, str | None]] = OrderedDict()

    @property
    def contact_tools(self) -> ContactTools:
        """Get or create ContactTools instance."""
        if self._contact_tools is None:
            self._contact_tools = ContactTools(self._link.rest)
        return self._contact_tools

    @property
    def hub_room_id(self) -> str | None:
        """Get the hub room ID if created."""
        return self._hub_room_id

    async def initialize_hub_room(self) -> str:
        """
        Initialize hub room at startup.

        Creates the hub room via REST. The room_added WebSocket event
        will trigger ExecutionContext creation. Call mark_hub_room_ready()
        when ExecutionContext is created.

        Returns:
            Hub room ID
        """
        async with self._hub_room_lock:
            if self._hub_room_id is not None:
                return self._hub_room_id

            logger.info(
                "Creating hub room for contact events at startup (task_id=%s)",
                self._config.hub_task_id or "none",
            )
            response = await self._link.rest.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(task_id=self._config.hub_task_id or None),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            self._hub_room_id = response.data.id
            logger.info("Hub room created at startup: %s", self._hub_room_id)
            return self._hub_room_id

    def mark_hub_room_ready(self) -> None:
        """Mark hub room as ready (ExecutionContext exists)."""
        self._hub_room_ready.set()
        logger.info("Hub room marked as ready: %s", self._hub_room_id)

    async def handle(self, event: ContactEvent) -> None:
        """
        Handle a contact event based on configured strategy.

        Args:
            event: Contact event to handle
        """
        logger.debug(
            "ContactEventHandler.handle: %s (strategy=%s)",
            type(event).__name__,
            self._config.strategy.value,
        )

        # Skip if already processed (deduplication)
        if self._should_skip_duplicate(event):
            logger.debug(
                "Skipping duplicate contact event: %s", self._get_dedup_key(event)
            )
            return

        # Cache request info for later enrichment of update events
        self._cache_request_info(event)

        # Handle broadcast if enabled (for contact_added/contact_removed)
        if self._config.broadcast_changes:
            await self._maybe_broadcast(event)

        # Route based on strategy
        success = True
        match self._config.strategy:
            case ContactEventStrategy.DISABLED:
                logger.debug("Contact event ignored (strategy=DISABLED)")
                return

            case ContactEventStrategy.CALLBACK:
                success = await self._handle_callback(event)

            case ContactEventStrategy.HUB_ROOM:
                success = await self._handle_hub_room(event)

        # Mark as processed only after successful handling
        if success:
            self._mark_processed(event)

    async def _handle_callback(self, event: ContactEvent) -> bool:
        """
        Handle event via CALLBACK strategy.

        Calls the configured on_event callback with event and tools.
        Exceptions are logged but not re-raised to avoid breaking the event loop.

        Returns:
            True if callback succeeded, False if it failed
        """
        if self._config.on_event is None:
            logger.warning("CALLBACK strategy but no on_event callback configured")
            return True  # Not a failure, just misconfigured

        try:
            logger.debug(
                "Calling contact event callback for %s",
                type(event).__name__,
            )
            await self._config.on_event(event, self.contact_tools)
            logger.debug("Contact event callback completed successfully")
            return True
        except Exception as e:
            # Log error but don't re-raise - we don't want to break the event loop
            logger.error("Contact event callback failed: %s", e, exc_info=True)
            return False

    async def _handle_hub_room(self, event: ContactEvent) -> bool:
        """
        Handle event via HUB_ROOM strategy.

        Routes event to dedicated hub room for LLM reasoning:
        1. Waits for hub room to be ready (created at startup)
        2. Injects synthetic MessageEvent into ExecutionContext queue
        3. Posts task event to room via REST for persistence/visibility

        Returns:
            True if event was routed successfully
        """
        # Auto-initialize hub room if not already done
        if self._hub_room_id is None:
            logger.info("Hub room not initialized, initializing now")
            await self.initialize_hub_room()

        if self._on_hub_event is None:
            logger.warning("HUB_ROOM strategy but no on_hub_event callback configured")
            return False

        try:
            # Wait for hub room to be ready (ExecutionContext exists)
            # Use a short timeout since hub room should be ready by now
            try:
                await asyncio.wait_for(self._hub_room_ready.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "Hub room not ready after 5s, proceeding anyway (may fail)"
                )

            hub_id = self._hub_room_id
            if hub_id is None:
                logger.error("Hub room initialization failed")
                return False

            # Inject system prompt on first use
            if not self._hub_room_initialized and self._on_hub_init:
                logger.info("Initializing hub room with system prompt")
                await self._on_hub_init(hub_id, HUB_ROOM_SYSTEM_PROMPT)
                self._hub_room_initialized = True

            # Format event as a message for LLM processing
            content = await self._format_event_for_room(event)
            event_type = self._get_event_type(event)

            # Create synthetic MessageEvent for queue injection
            now = datetime.now(timezone.utc).isoformat()
            message_event = MessageEvent(
                type="message_created",
                room_id=hub_id,
                payload=MessageCreatedPayload(
                    id=str(uuid.uuid4()),
                    content=content,
                    message_type="text",
                    sender_type=SYNTHETIC_SENDER_TYPE,
                    sender_id=SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
                    sender_name=SYNTHETIC_CONTACT_EVENTS_SENDER_NAME,
                    metadata=MessageMetadata(),
                    chat_room_id=hub_id,
                    inserted_at=now,
                    updated_at=now,
                ),
                raw={"contact_event_type": event_type},
            )

            # Push to hub room's execution context (triggers agent processing)
            logger.debug(
                "Injecting contact event to hub room %s: %s", hub_id, event_type
            )
            await self._on_hub_event(hub_id, message_event)

            # Also POST as task event to room for persistence/visibility
            await self._post_task_event(hub_id, content, event_type)

            logger.debug("Contact event injected to hub room successfully")
            return True

        except Exception as e:
            logger.error(
                "Failed to inject contact event to hub room: %s", e, exc_info=True
            )
            return False

    async def _post_task_event(
        self, room_id: str, content: str, event_type: str
    ) -> None:
        """
        Post contact event as task event to room via REST.

        This persists the event in the room's history and makes it visible in UI.

        Args:
            room_id: Hub room ID
            content: Formatted contact event content
            event_type: Contact event type for metadata
        """
        try:
            await self._link.rest.agent_api_events.create_agent_chat_event(
                chat_id=room_id,
                event=ChatEventRequest(
                    content=content,
                    message_type="task",
                    metadata={"contact_event_type": event_type},
                ),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            logger.debug("Task event posted to hub room: %s", event_type)
        except Exception as e:
            # Log but don't fail - the queue injection is the important part
            logger.warning("Failed to post task event to hub room: %s", e)

    async def _format_event_for_room(self, event: ContactEvent) -> str:
        """
        Format contact event as human-readable message for hub room.

        Args:
            event: Contact event to format

        Returns:
            Formatted message string
        """
        match event:
            case ContactRequestReceivedEvent(payload=payload):
                if payload is None:
                    return "[Contact Request] Unknown sender"
                from_handle = normalize_handle(payload.from_handle)
                msg_part = f'\nMessage: "{payload.message}"' if payload.message else ""
                return (
                    f"[Contact Request] {payload.from_name} ({from_handle}) "
                    f"wants to connect.{msg_part}\n"
                    f"Request ID: {payload.id}"
                )

            case ContactRequestUpdatedEvent(payload=payload):
                if payload is None:
                    return "[Contact Request Update] Unknown request"
                # Try to get enriched request info (cache + API fallback)
                info = await self._enrich_update_event(event)
                if info:
                    # Could be from received (from_*) or sent (to_*) request
                    name = info.get("from_name") or info.get("to_name")
                    raw_handle = info.get("from_handle") or info.get("to_handle") or ""
                    handle = normalize_handle(raw_handle) or ""
                    if name:
                        direction = "from" if info.get("from_name") else "to"
                        return (
                            f"[Contact Request Update] Request {direction} {name} "
                            f"({handle}) status changed to: {payload.status}\n"
                            f"Request ID: {payload.id}"
                        )
                return (
                    f"[Contact Request Update] Request {payload.id} "
                    f"status changed to: {payload.status}"
                )

            case ContactAddedEvent(payload=payload):
                if payload is None:
                    return "[Contact Added] Unknown contact"
                handle = normalize_handle(payload.handle)
                return (
                    f"[Contact Added] {payload.name} ({handle}) "
                    f"is now a contact.\n"
                    f"Type: {payload.type}, ID: {payload.id}"
                )

            case ContactRemovedEvent(payload=payload):
                if payload is None:
                    return "[Contact Removed] Unknown contact"
                return f"[Contact Removed] Contact {payload.id} was removed."

            case _:
                return f"[Contact Event] Unknown event type: {type(event).__name__}"

    def _get_event_type(self, event: ContactEvent) -> str:
        """
        Get the event type name for metadata.

        Args:
            event: Contact event

        Returns:
            Event type string
        """
        match event:
            case ContactRequestReceivedEvent():
                return "contact_request_received"
            case ContactRequestUpdatedEvent():
                return "contact_request_updated"
            case ContactAddedEvent():
                return "contact_added"
            case ContactRemovedEvent():
                return "contact_removed"
            case _:
                return "unknown"

    async def _maybe_broadcast(self, event: ContactEvent) -> None:
        """
        Queue broadcast message if applicable.

        Only broadcasts contact_added and contact_removed events.
        """
        if self._on_broadcast is None:
            return

        match event:
            case ContactAddedEvent(payload=payload):
                if payload is not None:
                    handle = normalize_handle(payload.handle)
                    msg = f"{handle} ({payload.name}) is now a contact"
                    self._on_broadcast(msg)
                    logger.debug("Queued broadcast: %s", msg)

            case ContactRemovedEvent(payload=payload):
                if payload is not None:
                    msg = f"Contact {payload.id} was removed"
                    self._on_broadcast(msg)
                    logger.debug("Queued broadcast: %s", msg)

            case _:
                # Don't broadcast request events
                pass

    def _should_skip_duplicate(self, event: ContactEvent) -> bool:
        """
        Check if event was already processed.

        Args:
            event: Event to check

        Returns:
            True if event was already processed
        """
        key = self._get_dedup_key(event)
        if key is None:
            return False
        return key in self._processed_events

    def _get_dedup_key(self, event: ContactEvent) -> str | None:
        """
        Get deduplication key for an event.

        Args:
            event: Event to get key for

        Returns:
            Unique key for deduplication, or None if event can't be deduplicated
        """
        match event:
            case ContactRequestReceivedEvent(payload=payload):
                return f"request_received:{payload.id}" if payload else None

            case ContactRequestUpdatedEvent(payload=payload):
                return (
                    f"request_updated:{payload.id}:{payload.status}"
                    if payload
                    else None
                )

            case ContactAddedEvent(payload=payload):
                return f"contact_added:{payload.id}" if payload else None

            case ContactRemovedEvent(payload=payload):
                return f"contact_removed:{payload.id}" if payload else None

            case _:
                return None

    def _mark_processed(self, event: ContactEvent) -> None:
        """
        Mark event as processed.

        Maintains bounded cache with LRU eviction.
        """
        key = self._get_dedup_key(event)
        if key is None:
            return

        # Add to cache
        self._processed_events[key] = True

        # Evict oldest entries if cache is too large
        while len(self._processed_events) > MAX_DEDUP_CACHE_SIZE:
            self._processed_events.popitem(last=False)

    def _clear_from_dedup(self, event: ContactEvent) -> None:
        """
        Clear event from deduplication cache.

        Called when callback fails so event can be retried.
        """
        key = self._get_dedup_key(event)
        if key is not None:
            self._processed_events.pop(key, None)

    def _cache_request_info(self, event: ContactEvent) -> None:
        """
        Cache request info for enriching update events.

        When ContactRequestReceivedEvent arrives, cache sender info.
        This allows ContactRequestUpdatedEvent to include full sender details.
        """
        match event:
            case ContactRequestReceivedEvent(payload=payload):
                if payload is None:
                    return
                self._request_cache[payload.id] = {
                    "from_handle": payload.from_handle,
                    "from_name": payload.from_name,
                    "message": payload.message,
                }
                # Maintain bounded cache size
                while len(self._request_cache) > MAX_DEDUP_CACHE_SIZE:
                    self._request_cache.popitem(last=False)
            case _:
                pass  # Only cache received events

    def _get_cached_request_info(self, request_id: str) -> dict[str, str | None] | None:
        """Get cached request info by ID."""
        return self._request_cache.get(request_id)

    async def _fetch_request_details(
        self, request_id: str
    ) -> dict[str, str | None] | None:
        """
        Fetch request details from API when cache misses.

        Queries both sent and received contact requests to find the matching one.

        Args:
            request_id: The request ID to look up

        Returns:
            Dict with from_handle, from_name, to_handle, to_name, message if found
        """
        try:
            response = (
                await self._link.rest.agent_api_contacts.list_agent_contact_requests(
                    page=1, page_size=100
                )
            )

            if not response.data:
                return None

            # Check received requests (from_handle, from_name)
            if response.data.received:
                for req in response.data.received:
                    if req.id == request_id:
                        info = {
                            "from_handle": req.from_handle,
                            "from_name": req.from_name,
                            "message": req.message,
                        }
                        # Cache for future use
                        self._request_cache[request_id] = info
                        logger.debug(
                            "Fetched request details from API (received): %s",
                            request_id,
                        )
                        return info

            # Check sent requests (to_handle, to_name)
            if response.data.sent:
                for req in response.data.sent:
                    if req.id == request_id:
                        info = {
                            "to_handle": req.to_handle,
                            "to_name": req.to_name,
                            "message": req.message,
                        }
                        # Cache for future use
                        self._request_cache[request_id] = info
                        logger.debug(
                            "Fetched request details from API (sent): %s", request_id
                        )
                        return info

            logger.debug("Request not found in API: %s", request_id)
            return None

        except Exception as e:
            logger.warning("Failed to fetch request details from API: %s", e)
            return None

    async def _enrich_update_event(
        self, event: ContactRequestUpdatedEvent
    ) -> dict[str, str | None] | None:
        """
        Get enriched info for an update event.

        Tries cache first, falls back to API fetch.

        Args:
            event: The update event to enrich

        Returns:
            Dict with sender/recipient info if found
        """
        if event.payload is None:
            return None

        request_id = event.payload.id

        # Try cache first
        cached = self._get_cached_request_info(request_id)
        if cached:
            return cached

        # Cache miss - fetch from API
        logger.debug("Cache miss for request %s, fetching from API", request_id)
        return await self._fetch_request_details(request_id)

    def get_stats(self) -> dict[str, Any]:
        """
        Get handler statistics.

        Returns:
            Dict with strategy, cache size, hub room ID
        """
        return {
            "strategy": self._config.strategy.value,
            "dedup_cache_size": len(self._processed_events),
            "hub_room_id": self._hub_room_id,
            "broadcast_enabled": self._config.broadcast_changes,
        }
