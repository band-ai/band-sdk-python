"""
RoomPresence - Cross-room lifecycle management.

Extracted from BandAgent room lifecycle methods.
Handles agent's presence across rooms. Does NOT handle what happens inside rooms.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from band_sdk_core import RoomMembership, RoomRoster

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.platform.event import (
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
    ReconnectedEvent,
    PlatformEvent,
    WebSocketDisconnectedEvent,
    ContactEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
)
from band.platform.link import BandLink
from band.runtime.tools import iter_chat_pages

# Type alias for contact event callback (agent-level, no tools)
ContactEventHandler = Callable[[ContactEvent], Awaitable[None]]

logger = logging.getLogger(__name__)


class RoomPresence:
    """
    Manages agent's presence across rooms.

    Cross-room only. Does NOT handle what happens inside rooms.
    That's the job of Execution implementations.

    Extracted from BandAgent room lifecycle:
    - _on_room_added() -> on_room_joined callback
    - _on_room_removed() -> on_room_left callback
    - _subscribe_to_existing_rooms() -> start() auto-subscription

    Example:
        import logging
        logger = logging.getLogger(__name__)

        link = BandLink(agent_id, api_key, ...)
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
        link: BandLink,
        room_filter: Callable[[dict], bool] | None = None,
        auto_subscribe_existing: bool = True,
    ):
        """
        Initialize RoomPresence.

        Args:
            link: BandLink for WebSocket events
            room_filter: Optional filter to decide which rooms to join
            auto_subscribe_existing: Subscribe to existing rooms on start
        """
        self.link = link
        self.room_filter = room_filter
        self.auto_subscribe_existing = auto_subscribe_existing

        # Track rooms we're present in
        self.roster = RoomRoster()

        # Callbacks (set by user or AgentRuntime)
        self.on_room_joined: Callable[[str, dict], Awaitable[None]] | None = None
        self.on_room_left: Callable[[str], Awaitable[None]] | None = None
        self.on_room_event: Callable[[str, PlatformEvent], Awaitable[None]] | None = (
            None
        )
        self.on_contact_event: ContactEventHandler | None = None
        self.on_reconnected: Callable[[], Awaitable[None]] | None = None
        self.on_disconnected: Callable[[], Awaitable[None]] | None = None

        # Internal task for consuming events from link
        self._event_task: asyncio.Task | None = None

    async def _notify(
        self,
        callback: Callable[..., Awaitable[None]] | None,
        *args: Any,
        label: str,
        level: int = logging.WARNING,
        exc_info: bool = False,
    ) -> None:
        """Invoke an optional user callback; a raise there is the caller's
        problem, never ours to propagate."""
        if callback is None:
            return
        try:
            await callback(*args)
        except Exception as e:
            logger.log(level, "%s callback error: %s", label, e, exc_info=exc_info)

    async def start(self) -> None:
        """
        Start presence management.

        1. Connect link if not connected
        2. Subscribe to agent room events
        3. Subscribe to existing rooms (if configured)
        4. Spawn task to consume events from link
        """
        if self._event_task is not None and not self._event_task.done():
            raise RuntimeError(
                f"RoomPresence for agent {self.link.agent_id} is already running; "
                "call stop() before starting again"
            )

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

        admitted_room_ids = self.roster.tracked_room_ids()
        self.roster.clear()
        await self._leave_and_notify(admitted_room_ids, context="stop")
        logger.info("RoomPresence stopped")

    async def _on_platform_event(self, event: PlatformEvent) -> None:
        """
        Handle platform events from BandLink.

        Routes to appropriate handler based on event type.
        """
        match event:
            case RoomAddedEvent():
                await self._handle_room_added(event)
            case RoomRemovedEvent() | RoomDeletedEvent():
                await self._handle_room_left(event)
            case ReconnectedEvent():
                await self._handle_reconnect()
            case WebSocketDisconnectedEvent():
                # Terminal for this connection (e.g. another consumer of the
                # same key superseded it). The transport will not recover by
                # itself, so the owner must be told rather than left waiting.
                await self._notify(self.on_disconnected, label="on_disconnected")
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

        Extracted from BandAgent._on_room_added().
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

        await self._join_room(room_id, payload, context="room_added")

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

        # Unconditional, harmless no-op if never subscribed.
        await self.link.unsubscribe_room(room_id)
        if not self.roster.record_room_removed(room_id):
            logger.debug(
                "%s event for untracked room %s, ignoring", event.type, room_id
            )
            return

        await self._notify(
            self.on_room_left,
            room_id,
            label="on_room_left",
            level=logging.ERROR,
            exc_info=True,
        )
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
        try:
            try:
                rooms_from_api = await self._list_existing_rooms()
            except Exception as e:
                logger.warning("Failed to sync rooms after reconnect: %s", e)
                return

            reconciliation = self.roster.reconcile(
                self._reconcile_target_room_ids(rooms_from_api)
            )
            await self._leave_removed_rooms(reconciliation.removed)
            await self._admit_reconciled_rooms(reconciliation.admitting, rooms_from_api)
            await self._notify_resync(reconciliation.resync)
        finally:
            # Notify callers so they can resync /next for messages missed during downtime
            await self._notify(self.on_reconnected, label="on_reconnected")

    def _reconcile_target_room_ids(
        self, rooms_from_api: dict[str, dict[str, Any]]
    ) -> list[str]:
        """The snapshot to diff against: every current room when auto-subscribing
        new ones, otherwise only rooms already admitted — so a room we were never
        asked to join can't enter reconcile()'s atomic admission-claim at all."""
        if self.auto_subscribe_existing:
            return list(rooms_from_api.keys())
        tracked = set(self.roster.tracked_room_ids())
        return [room_id for room_id in rooms_from_api if room_id in tracked]

    async def _leave_removed_rooms(self, room_ids: list[str]) -> None:
        """Unsubscribe and notify for every room reconcile() found gone.
        These were Admitted by construction (reconcile partitions
        admitted_rooms), so no was_admitted gating is needed here."""
        await self._leave_and_notify(room_ids, context="reconnect")

    async def _leave_and_notify(self, room_ids: list[str], *, context: str) -> None:
        """Unsubscribe and fire on_room_left for each room, in parallel —
        mirrors the admit side's fan-out (unsubscribe_room is keyed entirely
        by room_id, so concurrent calls for different rooms touch no shared
        state). Shared by stop() and _leave_removed_rooms, which differ only
        in why the rooms are going away."""
        if not room_ids:
            return
        await asyncio.gather(
            *[self._leave_one_room(room_id, context=context) for room_id in room_ids]
        )

    async def _leave_one_room(self, room_id: str, *, context: str) -> None:
        try:
            await self.link.unsubscribe_room(room_id)
        except Exception as e:
            logger.warning(
                "Failed to unsubscribe room %s during %s: %s", room_id, context, e
            )
        await self._notify(self.on_room_left, room_id, label="on_room_left")

    async def _admit_reconciled_rooms(
        self,
        admitting: list[tuple[str, int]],
        rooms_from_api: dict[str, dict[str, Any]],
    ) -> None:
        """Resolve every ticket reconcile() pre-claimed for a newly discovered
        room, in parallel — mirrors _subscribe_rooms's old fan-out."""
        if not admitting:
            return
        results = await asyncio.gather(
            *[
                self._complete_room_admission(
                    room_id, ticket, rooms_from_api[room_id], context="reconnect"
                )
                for room_id, ticket in admitting
            ]
        )
        self._log_admission_results(results, context="reconnect")

    async def _notify_resync(self, room_ids: list[str]) -> None:
        """Tell surviving rooms to resync. reconcile() already sorts these."""
        if not self.on_room_event:
            return
        reconnect_event = ReconnectedEvent()
        for room_id in room_ids:
            await self._notify(
                self.on_room_event, room_id, reconnect_event, label="on_room_event"
            )

    async def _handle_room_event(self, event: PlatformEvent) -> None:
        """
        Handle room-specific events (message, participant changes).

        Forwards to on_room_event callback.
        """
        room_id = event.room_id
        if not room_id:
            return

        # Only forward events for rooms we're tracking
        if self.roster.room_membership(room_id) is not RoomMembership.Admitted:
            logger.debug("Event for untracked room %s, ignoring", room_id)
            return

        await self._notify(
            self.on_room_event,
            room_id,
            event,
            label="on_room_event",
            level=logging.ERROR,
            exc_info=True,
        )

    async def _handle_contact_event(self, event: ContactEvent) -> None:
        """
        Handle contact events (requests, added, removed).

        Contact events have no room context and are agent-level.
        Forwards to on_contact_event callback.
        """
        await self._notify(
            self.on_contact_event,
            event,
            label=f"on_contact_event ({type(event).__name__})",
            level=logging.ERROR,
            exc_info=True,
        )

    async def _list_existing_rooms(self) -> dict[str, dict[str, Any]]:
        """Every current room the filter accepts, keyed by room ID.

        Keyed rather than listed because the listing is offset-paginated: a
        room added while it pages shifts the rest along, so one room can come
        back on two pages.
        """

        async def fetch(page: int, page_size: int) -> Any:
            return await self.link.rest.agent_api_chats.list_agent_chats(
                page=page,
                page_size=page_size,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )

        rooms: dict[str, dict[str, Any]] = {}
        async for response in iter_chat_pages(fetch):
            for room in response.data or []:
                payload = room.model_dump(exclude_none=True)
                if self.room_filter and not self.room_filter(payload):
                    continue
                rooms[room.id] = payload
        return rooms

    async def _join_room(
        self,
        room_id: str,
        payload: dict[str, Any],
        *,
        context: str,
    ) -> bool:
        """Claim admission for one room, at most once, and resolve it.

        The startup snapshot and a ``room_added`` event can name the same room,
        because the agent's room channel is live before the snapshot is read.
        Claiming the room via ``begin_room_admission`` before the first await is
        what makes the second caller a no-op instead of a second channel join
        and a second ``on_room_joined``.
        """
        ticket = self.roster.begin_room_admission(room_id, passes_filter=True)
        if ticket is None:
            logger.debug("Already joined room %s, ignoring %s", room_id, context)
            return False
        return await self._complete_room_admission(
            room_id, ticket, payload, context=context
        )

    async def _complete_room_admission(
        self,
        room_id: str,
        ticket: int,
        payload: dict[str, Any],
        *,
        context: str,
    ) -> bool:
        """Subscribe to a claimed room and resolve its ticket, whatever happens.

        ``record_room_admission`` is a safe no-op on an already-resolved/stale
        ticket, so a bare ``finally`` is enough to roll a cancelled or failed
        subscribe back to ``Unadmitted`` — no extra "settled" bookkeeping needed.
        Its return value still matters on the success path though: a ticket
        can go stale mid-flight (e.g. ``stop()``'s ``roster.clear()`` racing
        this call before ``self._event_task`` exists to be cancelled), and a
        stale-but-succeeded subscribe must not announce a room the roster no
        longer considers ours.
        """
        succeeded = False
        try:
            try:
                await self.link.subscribe_room(room_id)
            except Exception as e:
                logger.warning(
                    "Failed to subscribe to room %s during %s: %s", room_id, context, e
                )
                return False

            if not self.link.is_room_subscribed(room_id):
                # subscribe_room() is best-effort and non-raising by design (a
                # single room failure must not crash the whole subscription
                # sequence), so an internal join/rollback failure never reaches
                # this except block above — check the real outcome instead of
                # assuming "no exception" means "subscribed".
                logger.warning("Room %s did not subscribe during %s", room_id, context)
                return False

            succeeded = True
        finally:
            admitted = self.roster.record_room_admission(room_id, ticket, succeeded)

        if not admitted:
            logger.debug(
                "Admission ticket for room %s went stale during %s, ignoring",
                room_id,
                context,
            )
            return False

        # A callback that raises is the caller's problem, not a failed join:
        # the room is subscribed either way, and untracking it here would drop
        # every event it goes on to deliver.
        await self._notify(
            self.on_room_joined,
            room_id,
            payload,
            label="on_room_joined",
            level=logging.ERROR,
            exc_info=True,
        )

        logger.info("Agent joined room: %s", room_id)
        return True

    async def _subscribe_rooms(
        self,
        rooms_to_join: dict[str, dict[str, Any]],
        *,
        context: str,
    ) -> None:
        """Join every room in parallel."""
        if not rooms_to_join:
            return

        results = await asyncio.gather(
            *[
                self._join_room(room_id, payload, context=context)
                for room_id, payload in rooms_to_join.items()
            ],
        )
        self._log_admission_results(results, context=context)

    def _log_admission_results(self, results: list[bool], *, context: str) -> None:
        """Aggregate succeeded/failed summary for one batch of parallel
        room admissions — shared by _subscribe_rooms and
        _admit_reconciled_rooms."""
        succeeded = sum(results)
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
