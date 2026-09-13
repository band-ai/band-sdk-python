"""
BandLink - Live link to Band platform.

WebSocket connection and event dispatch. REST client exposed directly
for API calls.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from band.client.rest import AsyncRestClient
from band.config.settings import DEFAULT_REST_URL, DEFAULT_WS_URL
from band.client.streaming import WebSocketClient, WebSocketDisconnectReason
from band.core.types import PlatformConnection
from band.platform.message_lifecycle import MessageLifecycle
from band.platform.subscriptions import SubscriptionManager
from band.runtime.types import PlatformMessage
from band_sdk_core import AgentTopicKind, SessionState

from band.platform.event import (
    MessageEvent,
    RoomAddedEvent,
    RoomRemovedEvent,
    RoomDeletedEvent,
    ReconnectedEvent,
    WebSocketDisconnectedEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
    PlatformEvent,
)

if TYPE_CHECKING:
    from band.client.streaming import (
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
        SupersedePayload,
        AgentControlPayload,
    )

logger = logging.getLogger(__name__)


class BandLink:
    """
    Live link to Band platform.

    Handles WebSocket connection and event dispatch. REST client exposed
    directly via self.rest for API calls.

    Example:
        import logging
        logger = logging.getLogger(__name__)

        link = BandLink(agent_id="...", api_key="...")
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
        ws_url: str = DEFAULT_WS_URL,
        rest_url: str = DEFAULT_REST_URL,
    ):
        self.agent_id = agent_id
        self.api_key = api_key
        self.ws_url = ws_url
        self.rest_url = rest_url

        # REST client - exposed directly
        self.rest = AsyncRestClient(api_key=api_key, base_url=rest_url)

        # Pure REST message-lifecycle operations (mark_*/report_activity/
        # get_next_message/get_stale_processing_messages) — no WebSocket
        # state, so it lives in its own class rather than this one.
        self._messages = MessageLifecycle()

        # WebSocket client
        self._ws: WebSocketClient | None = None
        self._is_connected = False
        # True for the span of connect() before _ws is assigned, so a second
        # concurrent connect() sees it even mid-handshake (see connect()).
        self._connecting = False

        # Room/agent-topic subscription tracking and reconnect reconciliation --
        # lives in its own class since it needs the live WebSocketClient, unlike
        # MessageLifecycle. `current_ws` is a getter, never a cached value, so
        # BandLink stays the single owner of `_ws` across reconnects.
        self._subscriptions_manager = SubscriptionManager(current_ws=lambda: self._ws)

        # Event queue for async iteration
        self._event_queue: asyncio.Queue[PlatformEvent] = asyncio.Queue(maxsize=1000)

        # Durable terminal disconnect reason for the current connection lifecycle.
        self._last_disconnect_reason: WebSocketDisconnectReason | None = None

        # Preemptive control-signal hook (interrupt/stop/play). Set by the
        # runtime. Invoked DIRECTLY from the WebSocket receive task — never via
        # the serialized _event_queue — so a control signal can act on a cycle
        # already in flight instead of queuing behind it.
        self.on_control: Callable[[AgentControlPayload], Awaitable[None]] | None = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def last_disconnect_reason(self) -> WebSocketDisconnectReason | None:
        """Most recent terminal WebSocket disconnect reason, if reported."""
        return self._last_disconnect_reason

    def to_platform_connection(self, agent_id: str) -> PlatformConnection:
        """Coordinates for injecting into an adapter (see ``Agent.start``).

        ``agent_id`` is taken explicitly rather than read off ``self.agent_id``:
        callers with their own notion of the runtime identity (e.g. a one-shot
        host) may pass a different value than what this link connected as.
        """
        return PlatformConnection(
            agent_id=agent_id,
            api_key=self.api_key,
            rest_url=self.rest_url,
            ws_url=self.ws_url,
        )

    # --- Async iterator protocol ---

    def __aiter__(self):
        """Return self to allow async iteration over events."""
        return self

    async def __anext__(self) -> PlatformEvent:
        """Get next event from the queue. Blocks until an event is available."""
        return await self._event_queue.get()

    # --- Connection lifecycle ---

    async def connect(self) -> None:
        if self._connecting or self._ws is not None:
            logger.warning("Already connected or connecting")
            return

        # Set synchronously, before the first await below, so a second
        # concurrent connect() sees it immediately -- _ws alone can't do
        # that job, since it's only assigned once fully connected below
        # (never a half-connected client a caller could act on).
        self._connecting = True
        try:
            self._last_disconnect_reason = None
            ws = WebSocketClient(
                self.ws_url,
                self.api_key,
                self.agent_id,
                on_reconnect=self._on_reconnected,
                on_disconnect=self._on_disconnected,
            )
            try:
                await ws.__aenter__()
                await self._join_agent_control_channel(ws)
            except BaseException:
                # BaseException, not Exception: a cancellation reaching one
                # of these awaits (e.g. the caller's own task being
                # cancelled) must still close the half-opened client, or it
                # leaks -- _ws itself was never assigned, so there's nothing
                # to roll back on that side.
                if ws.last_disconnect_reason is not None:
                    # A Session-classified terminal initial-connect failure
                    # (__aenter__ already called record_terminal_disconnect)
                    # -- surface it the same way a terminal supersede does,
                    # even though self._ws was never assigned.
                    self._last_disconnect_reason = ws.last_disconnect_reason
                await ws.__aexit__(None, None, None)
                raise

            self._ws = ws
            self._is_connected = True
            logger.info("Connected to platform")
        finally:
            self._connecting = False

    async def disconnect(self) -> None:
        if not self._ws:
            return

        try:
            await self._ws.leave_agent_control_channel(self.agent_id)
        except Exception as e:
            logger.warning("Error unsubscribing from agent_control: %s", e)

        await self._ws.__aexit__(None, None, None)
        self._ws = None
        self._is_connected = False
        self._subscriptions_manager.end_session()
        logger.info("Disconnected from platform")

    async def run_forever(self) -> None:
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.run_forever()

    async def _join_agent_control_channel(self, ws: WebSocketClient) -> None:
        """Shared join call for agent_control -- used by both the initial
        ``connect()`` join and ``_recover_agent_control``'s rejoin repair,
        so the callback wiring only needs to be kept in sync in one place."""
        await ws.join_agent_control_channel(
            self.agent_id,
            on_supersede=self._on_supersede,
            on_control=self._on_control,
        )

    # --- Subscription management ---

    async def subscribe_agent_rooms(self, agent_id: str) -> None:
        """Subscribe to agent room events (room_added/removed)."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._subscriptions_manager.subscribe_agent_rooms(
            self._ws,
            agent_id,
            on_room_added=self._on_room_added,
            on_room_removed=self._on_room_removed,
        )

    async def subscribe_room(self, room_id: str) -> None:
        """Subscribe to room messages and participants."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._subscriptions_manager.subscribe_room(
            self._ws,
            room_id,
            on_message_created=lambda msg: self._on_message_created(room_id, msg),
            on_participant_added=lambda p: self._on_participant_added(room_id, p),
            on_participant_removed=lambda p: self._on_participant_removed(room_id, p),
            on_room_deleted=lambda p: self._on_room_deleted(room_id, p),
        )

    async def subscribe_agent_contacts(self, agent_id: str) -> None:
        """Subscribe to agent contact events."""
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._subscriptions_manager.subscribe_agent_contacts(
            self._ws,
            agent_id,
            on_contact_request_received=self._on_contact_request_received,
            on_contact_request_updated=self._on_contact_request_updated,
            on_contact_added=self._on_contact_added,
            on_contact_removed=self._on_contact_removed,
        )

    async def unsubscribe_room(self, room_id: str) -> None:
        if not self._ws:
            return
        await self._subscriptions_manager.unsubscribe_room(self._ws, room_id)

    async def unsubscribe_agent_contacts(self) -> None:
        """Unsubscribe from agent contacts channel."""
        if not self._ws:
            return
        await self._subscriptions_manager.unsubscribe_agent_contacts(
            self._ws, self.agent_id
        )

    def is_room_subscribed(self, room_id: str) -> bool:
        """Whether ``room_id`` is currently fully subscribed (both topics)."""
        return self._subscriptions_manager.is_room_subscribed(room_id)

    def _connected_ws(self) -> WebSocketClient:
        """The active WebSocket client -- only ever called from the client's
        own reconnect hook (``_on_reconnected``) or something it calls
        synchronously from there, where a live connection is guaranteed."""
        if self._ws is None:
            raise RuntimeError("Not connected")
        return self._ws

    async def _recover_agent_control(
        self, ws: WebSocketClient, joined: frozenset[str]
    ) -> None:
        """agent_control is joined once in connect() and lives for the whole
        session, outside SubscriptionTracker entirely -- unlike the
        tracker-owned channels, there is no consumer-facing resubscribe call
        waiting to be unblocked, so a rejoin PHX itself rejected is repaired
        here directly instead of only flagged for later.

        ``joined`` is the same shared ``ws.joined_topics()`` snapshot
        ``_on_reconnected`` passes to the tracker-owned detection helpers.
        """
        topic = AgentTopicKind.Control.topic(self.agent_id)
        if topic in joined:
            return
        logger.warning("agent_control rejoin failed, attempting recovery")
        try:
            await self._join_agent_control_channel(ws)
            logger.info("Recovered agent_control after a rejected rejoin")
        except Exception as e:
            logger.warning("Failed to recover agent_control: %s", e)

    # --- Event handlers ---

    async def _on_reconnected(self) -> None:
        """Handle PHX client reconnection: PHXChannelsClient has already
        re-subscribed previously joined topics by the time this fires, so
        tracked rooms/topics can be reconciled against real server state
        here without replaying duplicate joins.

        Runs rejoin-failure detection before draining so a rejoin failure
        gets the same best-effort clean leave as every other ambiguous
        outcome (a cancelled join, a failed rollback) already queued for
        ``_drain_reconciliation`` -- this reconnect is the only point where
        that ambiguity is safely resolvable (see design doc).
        """
        logger.info("WebSocket reconnected — reconciling room state")
        self._subscriptions_manager.mark_reconnected()
        ws = self._connected_ws()
        joined = ws.joined_topics()
        await self._recover_agent_control(ws, joined)
        self._subscriptions_manager.detect_rejoin_failures(ws, joined)
        await self._drain_reconciliation()
        self._queue_event(ReconnectedEvent())

    async def _drain_reconciliation(self) -> None:
        """Force a clean transport + tracker state for every room/topic left
        ambiguous since the last reconnect, then release the local block.
        """
        await self._subscriptions_manager.drain_reconciliation(self._connected_ws())

    async def _on_supersede(self, payload: "SupersedePayload") -> None:
        """Handle an agent_control supersede event before the platform closes
        the socket. Session arbitrates whether this specific supersede is
        actually current (not a stale notification about an epoch this
        connection has already moved past) and, in principle, whether a
        retryable supersede should keep the connection reconnecting --
        though the platform hardcodes retryable=False today, so in practice
        this still always resolves to Dead.
        """
        if self._ws is not None:
            outcome = self._ws.handle_supersede(payload.retryable, payload.retry_after)
            reason = payload.to_disconnect_reason(outcome)
            if outcome.state is not SessionState.Dead:
                # outcome.retry_after_s is computed but intentionally not
                # applied here: enforcing it would mean this SDK taking over
                # firing the actual reconnect attempt, which is the vendored
                # PHXChannelsClient auto_reconnect loop's job today (see the
                # design doc's out-of-scope boundary) -- untouched reconnect
                # keeps working exactly as it does for any other disconnect.
                logger.info(
                    "Supersede reported retryable=True; Session kept the "
                    "connection reconnecting (state=%s, retry_after_s=%s), "
                    "not treating it as terminal.",
                    outcome.state,
                    outcome.retry_after_s,
                )
                return
            self._ws.record_terminal_disconnect(reason)
        else:
            reason = payload.to_disconnect_reason()
        self._last_disconnect_reason = reason
        self._is_connected = False
        logger.warning(
            "WebSocket connection superseded: reason=%s retryable=%s correlation_id=%s",
            reason.reason,
            reason.retryable,
            reason.correlation_id,
        )
        self._queue_event(WebSocketDisconnectedEvent(payload=reason))

    async def _on_control(self, payload: "AgentControlPayload") -> None:
        """Handle an ``agent.control`` push (interrupt/stop/play).

        Invoked directly from the WebSocket receive task. Forwards to the
        registered ``on_control`` hook WITHOUT touching the serialized event
        queue, so the signal can preempt a cycle already in flight. If no hook
        is registered, the push is a safe no-op.
        """
        if self.on_control is None:
            logger.debug(
                "agent.control received (mode=%s) but no on_control hook registered",
                payload.mode,
            )
            return
        await self.on_control(payload)

    async def _on_disconnected(self, error: Exception | None) -> None:
        """Handle PHX client disconnection."""
        if self.last_disconnect_reason:
            logger.warning(
                "WebSocket disconnected after terminal platform reason: %s",
                self.last_disconnect_reason.reason,
            )
            return
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
        event = RoomAddedEvent(
            room_id=payload.id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_room_removed(self, payload: "RoomRemovedPayload") -> None:
        event = RoomRemovedEvent(
            room_id=payload.id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_message_created(
        self, room_id: str, payload: "MessageCreatedPayload"
    ) -> None:
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
        """Payload is already validated by WebSocketClient._handle_events()."""
        event = ParticipantAddedEvent(
            room_id=room_id,
            payload=payload,
        )
        self._queue_event(event)

    async def _on_participant_removed(
        self, room_id: str, payload: "ParticipantRemovedPayload"
    ) -> None:
        """Payload is already validated by WebSocketClient._handle_events()."""
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
    #
    # Thin delegates to MessageLifecycle, which owns pure REST message
    # operations with no WebSocket state. ``self.rest`` is passed per call
    # (not captured once) so a caller that reassigns ``link.rest`` after
    # construction — every mocked test does — is still honored.

    async def mark_processing(self, room_id: str, message_id: str) -> bool:
        return await self._messages.mark_processing(self.rest, room_id, message_id)

    async def mark_processed(self, room_id: str, message_id: str) -> bool:
        return await self._messages.mark_processed(self.rest, room_id, message_id)

    async def mark_failed(self, room_id: str, message_id: str, error: str) -> bool:
        return await self._messages.mark_failed(self.rest, room_id, message_id, error)

    async def report_activity(
        self, room_id: str, working: bool, *, timeout_seconds: int = 2
    ) -> bool:
        return await self._messages.report_activity(
            self.rest, room_id, working, timeout_seconds=timeout_seconds
        )

    async def get_next_message(self, room_id: str) -> PlatformMessage | None:
        return await self._messages.get_next_message(self.rest, room_id)

    async def get_stale_processing_messages(
        self, room_id: str
    ) -> list[PlatformMessage]:
        return await self._messages.get_stale_processing_messages(self.rest, room_id)
