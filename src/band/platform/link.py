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
from band.runtime.types import PlatformMessage
from band_sdk_core import (
    AgentTopicKind,
    LeaveOutcome,
    RoomSubscribeResult,
    SubscriptionTracker,
    chat_room_topic,
    room_participants_topic,
)

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

        # Subscription tracking (band_sdk_core.SubscriptionTracker) plus local
        # bookkeeping for claims whose real-world outcome is ambiguous (a
        # cancelled join, a failed rollback, a non-clean leave) — drained only
        # at the next reconnect boundary, see _drain_reconciliation.
        self._subscriptions = SubscriptionTracker()
        self._rooms_needing_reconciliation: set[str] = set()
        self._agent_topics_needing_reconciliation: set[str] = set()

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
        self._subscriptions.end_session()
        self._rooms_needing_reconciliation.clear()
        self._agent_topics_needing_reconciliation.clear()
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

    def _blocked_by_reconciliation(
        self, key: str, pending: set[str], *, noun: str
    ) -> bool:
        """Whether ``key`` is blocked from a fresh subscribe until the next
        reconnect drains ``pending`` — the single check both subscribe_room
        and _subscribe_agent_topic gate on (see design doc for why this, not
        core's own status, is the authoritative block condition)."""
        if key not in pending:
            return False
        logger.warning(
            "%s %s needs reconciliation, blocking subscribe until next reconnect",
            noun,
            key,
        )
        return True

    def _is_current_session(self, ws: WebSocketClient) -> bool:
        """False once ``ws``'s connection has been torn down or replaced --
        its own pending entries are either already cleared or will never
        drain, so acting through it further would leak into a session that
        never touched this room/topic."""
        return self._ws is ws

    def _mark_needing_reconciliation(
        self, key: str, pending: set[str], ws: WebSocketClient
    ) -> None:
        """Block ``key`` from resubscribe until reconciled, within ``ws``'s
        session only."""
        if self._is_current_session(ws):
            pending.add(key)

    async def _leave_channel(
        self,
        leave: Callable[[], Awaitable[None]],
        *,
        description: str,
        level: int = logging.WARNING,
    ) -> bool:
        """Attempt one best-effort channel leave: log and swallow any
        failure, report whether it actually succeeded. Shared by every leave
        attempt in this module (rollback, unsubscribe, reconciliation drain)
        so "did we manage to leave this?" has one implementation."""
        try:
            await leave()
            return True
        except Exception as e:
            logger.log(level, "Error %s: %s", description, e)
            return False

    async def subscribe_agent_rooms(self, agent_id: str) -> None:
        """Subscribe to agent room events (room_added/removed)."""
        if not self._ws:
            raise RuntimeError("Not connected")
        ws = self._ws

        await self._subscribe_agent_topic(
            AgentTopicKind.Rooms.topic(agent_id),
            lambda: ws.join_agent_rooms_channel(
                agent_id,
                on_room_added=self._on_room_added,
                on_room_removed=self._on_room_removed,
            ),
            ws,
        )

    async def subscribe_room(self, room_id: str) -> None:
        """Subscribe to room messages and participants.

        Blocked (a no-op, logged) while ``room_id`` is in
        ``_rooms_needing_reconciliation`` — a prior cancelled/ambiguous
        attempt's outcome is unresolved and must not be retried on the same
        socket. Stays blocked until the next reconnect drains it (see
        ``_drain_reconciliation``); see the design doc for why.
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        ws = self._ws

        if self._blocked_by_reconciliation(
            room_id, self._rooms_needing_reconciliation, noun="Room"
        ):
            return

        ticket = self._subscriptions.begin_room_subscribe(room_id=room_id)
        if ticket is None:
            return

        settled = False
        try:
            try:
                # Subscribe to messages
                await ws.join_chat_room_channel(
                    room_id,
                    on_message_created=lambda msg: self._on_message_created(
                        room_id, msg
                    ),
                )
            except Exception as e:
                logger.warning("Failed to join chat_room:%s: %s", room_id, e)
                self._subscriptions.record_chat_room_join_failed(
                    room_id=room_id, ticket=ticket
                )
                settled = True
                return

            try:
                # Subscribe to participant updates
                await ws.join_room_participants_channel(
                    room_id,
                    on_participant_added=lambda p: self._on_participant_added(
                        room_id, p
                    ),
                    on_participant_removed=lambda p: self._on_participant_removed(
                        room_id, p
                    ),
                    on_room_deleted=lambda p: self._on_room_deleted(room_id, p),
                )
            except Exception as e:
                logger.warning("Failed to join room_participants:%s: %s", room_id, e)
                # Clean up the chat_room channel we already joined. Logged at
                # DEBUG here (not WARNING) so a rollback failure produces one
                # WARNING below, not two for the same event — the exception
                # detail is still available for diagnosis.
                chat_room_left = await self._leave_channel(
                    lambda: ws.leave_chat_room_channel(room_id),
                    description=f"rolling back chat_room:{room_id}",
                    level=logging.DEBUG,
                )
                result = self._subscriptions.record_room_participants_join_failed(
                    room_id=room_id, ticket=ticket, chat_room_left=chat_room_left
                )
                settled = True
                if result is RoomSubscribeResult.RollbackFailed:
                    logger.warning(
                        "Rollback failed for room %s after participants-join "
                        "failure; needs reconciliation on next reconnect",
                        room_id,
                    )
                    self._mark_needing_reconciliation(
                        room_id, self._rooms_needing_reconciliation, ws
                    )
                return

            self._subscriptions.record_both_room_topics_joined(
                room_id=room_id, ticket=ticket
            )
            settled = True
            logger.debug("Subscribed to room %s", room_id)
        finally:
            # Cancellation (or any other unexpected escape) leaves the ticket
            # unresolved: force it into the one outcome that can express
            # ambiguity to core (see design doc). Only block local resubscribe
            # if this ticket was still current when it applied -- a stale
            # ticket (already resolved another way) must not mark reconciliation.
            if not settled:
                result = self._subscriptions.record_room_participants_join_failed(
                    room_id=room_id, ticket=ticket, chat_room_left=False
                )
                if result is RoomSubscribeResult.RollbackFailed:
                    self._mark_needing_reconciliation(
                        room_id, self._rooms_needing_reconciliation, ws
                    )

    async def subscribe_agent_contacts(self, agent_id: str) -> None:
        """
        Subscribe to agent contact events.

        Events: contact_request_received, contact_request_updated,
                contact_added, contact_removed
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        ws = self._ws

        await self._subscribe_agent_topic(
            AgentTopicKind.Contacts.topic(agent_id),
            lambda: ws.join_agent_contacts_channel(
                agent_id,
                on_contact_request_received=self._on_contact_request_received,
                on_contact_request_updated=self._on_contact_request_updated,
                on_contact_added=self._on_contact_added,
                on_contact_removed=self._on_contact_removed,
            ),
            ws,
        )

    async def _subscribe_agent_topic(
        self, topic: str, join: Callable[[], Awaitable[None]], ws: WebSocketClient
    ) -> None:
        """Shared join/track/rollback shape for the single-topic agent
        channels (``agent_rooms``, ``agent_contacts``) — mirrors
        ``subscribe_room``'s two-topic version but with one join, no
        rollback phase.
        """
        if self._blocked_by_reconciliation(
            topic, self._agent_topics_needing_reconciliation, noun="Agent topic"
        ):
            return

        ticket = self._subscriptions.begin_agent_topic_join(topic=topic)
        if ticket is None:
            return

        settled = False
        try:
            await join()
            self._subscriptions.record_agent_topic_join(
                topic=topic, ticket=ticket, joined=True
            )
            settled = True
            logger.debug("Joined agent topic %s", topic)
        except Exception as e:
            logger.warning("Failed to join agent topic %s: %s", topic, e)
            self._subscriptions.record_agent_topic_join(
                topic=topic, ticket=ticket, joined=False
            )
            settled = True
        finally:
            # An unresolved ticket means the real transport outcome is
            # unknown (e.g. cancelled after PHX's own join call started) --
            # record_agent_topic_join_ambiguous resolves core straight to
            # NeedsReconciliation instead of Absent.
            if not settled:
                if self._subscriptions.record_agent_topic_join_ambiguous(
                    topic=topic, ticket=ticket
                ):
                    self._mark_needing_reconciliation(
                        topic, self._agent_topics_needing_reconciliation, ws
                    )

    async def unsubscribe_room(self, room_id: str) -> None:
        if not self._ws:
            return
        ws = self._ws

        ticket = self._subscriptions.unsubscribe_room(room_id=room_id)
        if ticket is None:
            return

        outcome = LeaveOutcome.Unknown
        try:
            chat_room_left = await self._leave_channel(
                lambda: ws.leave_chat_room_channel(room_id),
                description=f"unsubscribing from chat_room:{room_id}",
            )
            participants_left = await self._leave_channel(
                lambda: ws.leave_room_participants_channel(room_id),
                description=f"unsubscribing from room_participants:{room_id}",
            )

            outcome = (
                LeaveOutcome.Left
                if (chat_room_left and participants_left)
                else LeaveOutcome.Failed
            )
            logger.debug("Unsubscribed from room %s (outcome=%s)", room_id, outcome)
        finally:
            # A cancellation leaves `outcome` at its Unknown default — either
            # way this resolves the ticket exactly once.
            self._subscriptions.mark_room_leave_complete(
                room_id=room_id, ticket=ticket, outcome=outcome
            )
            if outcome is not LeaveOutcome.Left:
                self._mark_needing_reconciliation(
                    room_id, self._rooms_needing_reconciliation, ws
                )

    async def unsubscribe_agent_contacts(self) -> None:
        """Unsubscribe from agent contacts channel.

        A true no-op when the topic was never joined — the tracker's
        ``leave_agent_topic`` returns ``None`` in that case rather than
        issuing a leave the transport would just reject.
        """
        if not self._ws:
            return
        ws = self._ws

        await self._leave_agent_topic(
            AgentTopicKind.Contacts.topic(self.agent_id),
            lambda: ws.leave_agent_contacts_channel(self.agent_id),
            ws,
        )

    async def _leave_agent_topic(
        self, topic: str, leave: Callable[[], Awaitable[None]], ws: WebSocketClient
    ) -> None:
        """Shared leave/track shape for the single-topic agent channels."""
        ticket = self._subscriptions.leave_agent_topic(topic=topic)
        if ticket is None:
            return

        outcome = LeaveOutcome.Unknown
        try:
            left = await self._leave_channel(
                leave, description=f"leaving agent topic {topic}"
            )
            outcome = LeaveOutcome.Left if left else LeaveOutcome.Failed
            if left:
                logger.debug("Left agent topic %s", topic)
        finally:
            self._subscriptions.mark_agent_topic_leave_complete(
                topic=topic, ticket=ticket, outcome=outcome
            )
            if outcome is not LeaveOutcome.Left:
                self._mark_needing_reconciliation(
                    topic, self._agent_topics_needing_reconciliation, ws
                )

    def is_room_subscribed(self, room_id: str) -> bool:
        """Whether ``room_id`` is currently fully subscribed (both topics)."""
        return self._subscriptions.is_room_subscribed(room_id=room_id)

    def _connected_ws(self) -> WebSocketClient:
        """The active WebSocket client -- only ever called from the client's
        own reconnect hook (``_on_reconnected``) or something it calls
        synchronously from there, where a live connection is guaranteed."""
        if self._ws is None:
            raise RuntimeError("Not connected")
        return self._ws

    def _detect_room_rejoin_failures(
        self, ws: WebSocketClient, joined: frozenset[str]
    ) -> None:
        """PHXChannelsClient has no per-topic rejoin callback, so this
        compares its settled post-rejoin registry against the tracker's
        belief instead -- safe with no race, since PHX's own rejoin pass
        for every topic completes before this hook fires.

        Only catches an explicit rejoin rejection: a topic hit by a
        transient failure stays in PHX's own registry ("will retry on next
        reconnect"), so it still reads as joined here and is caught later
        if it ever fails for real.

        Reports each candidate's own generation ticket, so a room that was
        unsubscribed and re-subscribed between the failure and this check
        is left alone: the tracker rejects the stale ticket as a no-op.

        ``joined`` is a single ``ws.joined_topics()`` snapshot shared across
        this whole reconnect pass by ``_on_reconnected`` -- never taken here
        directly, so all detection in the pass sees the same registry state.
        """
        candidates = self._subscriptions.room_rejoin_candidates()
        if not candidates:
            return
        dropped = [
            (room_id, ticket)
            for room_id, ticket in candidates
            if not (
                chat_room_topic(room_id) in joined
                and room_participants_topic(room_id) in joined
            )
        ]
        for room_id, ticket in dropped:
            if self._subscriptions.mark_room_rejoin_failed(
                room_id=room_id, ticket=ticket
            ):
                self._mark_needing_reconciliation(
                    room_id, self._rooms_needing_reconciliation, ws
                )

    def _detect_agent_topic_rejoin_failures(
        self, ws: WebSocketClient, joined: frozenset[str]
    ) -> None:
        """Same rejoin-failure detection as ``_detect_room_rejoin_failures``,
        for the single-topic agent channels. ``joined`` is the same shared
        snapshot ``_detect_room_rejoin_failures`` receives."""
        candidates = self._subscriptions.agent_topic_rejoin_candidates()
        if not candidates:
            return
        dropped = [
            (topic, ticket) for topic, ticket in candidates if topic not in joined
        ]
        for topic, ticket in dropped:
            if self._subscriptions.mark_agent_topic_rejoin_failed(
                topic=topic, ticket=ticket
            ):
                self._mark_needing_reconciliation(
                    topic, self._agent_topics_needing_reconciliation, ws
                )

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
        self._subscriptions.on_reconnected()
        ws = self._connected_ws()
        joined = ws.joined_topics()
        await self._recover_agent_control(ws, joined)
        self._detect_room_rejoin_failures(ws, joined)
        self._detect_agent_topic_rejoin_failures(ws, joined)
        await self._drain_reconciliation()
        self._queue_event(ReconnectedEvent())

    async def _drain_reconciliation(self) -> None:
        """Force a clean transport + tracker state for every room/topic left
        ambiguous since the last reconnect, then release the local block.
        """
        ws = self._connected_ws()

        await self._drain_room_reconciliation(ws)
        await self._drain_agent_topic_reconciliation(ws)

    async def _drain_room_reconciliation(self, ws: WebSocketClient) -> None:
        """Stops as soon as ``ws``'s session ends mid-await -- never a
        correctness issue (every leave here is already best-effort), just
        pointless work through a client this session no longer owns."""
        for room_id in list(self._rooms_needing_reconciliation):
            if not self._is_current_session(ws):
                return
            await self._leave_channel(
                lambda: ws.leave_chat_room_channel(room_id),
                description=f"best-effort reconciliation leave of chat_room:{room_id}",
                level=logging.DEBUG,
            )
            await self._leave_channel(
                lambda: ws.leave_room_participants_channel(room_id),
                description=(
                    f"best-effort reconciliation leave of room_participants:{room_id}"
                ),
                level=logging.DEBUG,
            )
            self._subscriptions.acknowledge_room_reconciled(room_id=room_id)
            self._rooms_needing_reconciliation.discard(room_id)

    async def _drain_agent_topic_reconciliation(self, ws: WebSocketClient) -> None:
        """Same stale-session handling as ``_drain_room_reconciliation``."""
        for topic in list(self._agent_topics_needing_reconciliation):
            if not self._is_current_session(ws):
                return
            kind, _, agent_id = topic.partition(":")
            topic_kind = AgentTopicKind.from_wire_name(kind)
            if topic_kind is None:
                raise RuntimeError(f"Unrecognized agent topic kind: {topic!r}")
            leave = (
                ws.leave_agent_rooms_channel
                if topic_kind == AgentTopicKind.Rooms
                else ws.leave_agent_contacts_channel
            )
            await self._leave_channel(
                lambda: leave(agent_id),
                description=f"best-effort reconciliation leave of {topic}",
                level=logging.DEBUG,
            )
            self._subscriptions.acknowledge_agent_topic_reconciled(topic=topic)
            self._agent_topics_needing_reconciliation.discard(topic)

    async def _on_supersede(self, payload: "SupersedePayload") -> None:
        """Handle terminal supersede event before the platform closes the socket."""
        reason = payload.to_disconnect_reason()
        self._last_disconnect_reason = reason
        self._is_connected = False
        if self._ws:
            self._ws.record_terminal_disconnect(reason)
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
