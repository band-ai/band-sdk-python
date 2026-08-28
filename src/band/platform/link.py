"""
BandLink - Live link to Band platform.

Extracted from core/agent.py BandAgent - WebSocket management only.
REST client exposed directly for API calls.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from band.client.rest import AsyncRestClient
from band.config.settings import DEFAULT_REST_URL, DEFAULT_WS_URL
from band.client.streaming import WebSocketClient, WebSocketDisconnectReason
from band.client.streaming.client import chat_room_topic, room_participants_topic
from band.core.types import PlatformConnection
from band.platform.message_lifecycle import MessageLifecycle
from band.runtime.types import PlatformMessage
from band_sdk_core import LeaveOutcome, RoomSubscribeResult, SubscriptionTracker

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


# Single source of truth for the two single-topic agent-channel kinds: every
# site that builds a topic string (_agent_*_topic) or parses one back apart
# (_drain_reconciliation) reads these, so a typo in one can't silently
# diverge from the other.
_AGENT_ROOMS_KIND = "agent_rooms"
_AGENT_CONTACTS_KIND = "agent_contacts"


def _agent_rooms_topic(agent_id: str) -> str:
    return f"{_AGENT_ROOMS_KIND}:{agent_id}"


def _agent_contacts_topic(agent_id: str) -> str:
    return f"{_AGENT_CONTACTS_KIND}:{agent_id}"


class BandLink:
    """
    Live link to Band platform.

    Extracted from BandAgent - handles WebSocket connection and event dispatch.
    REST client exposed directly via self.rest for API calls.

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

        # REST client - exposed directly (from BandAgent._api_client)
        self.rest = AsyncRestClient(api_key=api_key, base_url=rest_url)

        # Pure REST message-lifecycle operations (mark_*/report_activity/
        # get_next_message/get_stale_processing_messages) — no WebSocket
        # state, so it lives in its own class rather than this one.
        self._messages = MessageLifecycle()

        # WebSocket client (from BandAgent._ws_client)
        self._ws: WebSocketClient | None = None
        self._is_connected = False

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

    # --- Connection lifecycle (from BandAgent.start/stop/run) ---

    async def connect(self) -> None:
        """
        Connect WebSocket.

        Extracted from BandAgent.start() lines 158-164.
        """
        if self._ws is not None:
            logger.warning("Already connected or connecting")
            return

        self._last_disconnect_reason = None
        self._ws = WebSocketClient(
            self.ws_url,
            self.api_key,
            self.agent_id,
            on_reconnect=self._on_reconnected,
            on_disconnect=self._on_disconnected,
        )
        await self._ws.__aenter__()
        try:
            await self._ws.join_agent_control_channel(
                self.agent_id,
                on_supersede=self._on_supersede,
                on_control=self._on_control,
            )
        except Exception:
            await self._ws.__aexit__(None, None, None)
            self._ws = None
            raise
        self._is_connected = True
        logger.info("Connected to platform")

    async def disconnect(self) -> None:
        """
        Disconnect WebSocket.

        Extracted from BandAgent.stop() lines 193-195.
        """
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
        """
        Run until interrupted.

        From BandAgent.run() lines 208-209.
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        await self._ws.run_forever()

    # --- Subscription management (from BandAgent) ---

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

    def _mark_needing_reconciliation(
        self, key: str, pending: set[str], ws: WebSocketClient
    ) -> None:
        """Block ``key`` from resubscribe until reconciled — but only within
        the connection session (``ws``) that produced the ambiguity. A
        session that has since been torn down or replaced (``self._ws`` no
        longer ``ws``) has already cleared, or will never drain, this entry
        on its own; adding it here would instead wrongly carry the block
        into a session that never touched this room/topic."""
        if self._ws is ws:
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
        """
        Subscribe to agent room events (room_added/removed).

        From BandAgent.start() lines 167-171.
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        ws = self._ws

        await self._subscribe_agent_topic(
            _agent_rooms_topic(agent_id),
            lambda: ws.join_agent_rooms_channel(
                agent_id,
                on_room_added=self._on_room_added,
                on_room_removed=self._on_room_removed,
            ),
            ws,
        )

    async def subscribe_room(self, room_id: str) -> None:
        """
        Subscribe to room messages and participants.

        Extracted from BandAgent._subscribe_to_room() lines 724-746.
        Wraps each channel join so a single room failure doesn't crash
        the entire subscription sequence.

        Blocked (a no-op, logged) while ``room_id`` is in
        ``_rooms_needing_reconciliation`` — the room's outcome from a prior
        cancelled/ambiguous attempt is unresolved and must not be retried on
        the same socket. It stays blocked until the next reconnect drains it
        (see ``_drain_reconciliation``); see the design doc for why.
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
                # Subscribe to messages (from lines 733-736)
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
                # Subscribe to participant updates (from lines 739-743)
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
            # ambiguity to core (see design doc), and block local resubscribe
            # until the next reconnect regardless of what core reports back.
            if not settled:
                self._subscriptions.record_room_participants_join_failed(
                    room_id=room_id, ticket=ticket, chat_room_left=False
                )
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
            _agent_contacts_topic(agent_id),
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
            # record_agent_topic_join(joined=False) never reaches core's own
            # NeedsReconciliation (see design doc) — the local set is the
            # only thing that blocks a same-socket retry here.
            if not settled:
                self._subscriptions.record_agent_topic_join(
                    topic=topic, ticket=ticket, joined=False
                )
                self._mark_needing_reconciliation(
                    topic, self._agent_topics_needing_reconciliation, ws
                )

    async def unsubscribe_room(self, room_id: str) -> None:
        """
        Unsubscribe from room.

        Extracted from BandAgent._unsubscribe_from_room() lines 748-769.
        """
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
            _agent_contacts_topic(self.agent_id),
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

    def _detect_room_rejoin_failures(self, ws: WebSocketClient) -> None:
        """A room's chat_room/room_participants topic can fail to rejoin
        after a reconnect with no callback into anything BandLink-owned —
        the Phoenix client has only connection-level hooks, no topic-level
        one — so this compares the transport's settled post-rejoin
        registry against the tracker's belief instead. Safe to read here
        with no race: PHXChannelsClient's own rejoin attempt for each topic
        fully completes before this reconnect hook ever fires.

        Only catches an *explicit* rejoin rejection (the server actually
        replying no): PHXChannelsClient keeps a topic that hit a transient
        failure (a join timeout, or the connection dropping mid-rejoin) in
        its own subscription registry rather than unregistering it, logging
        "will retry on next reconnect" — so this detector correctly sees it
        as still joined. That topic isn't lost track of, just not caught by
        this pass: the client itself retries it on the next reconnect,
        which either succeeds or eventually produces a real rejection this
        detector does catch.
        """
        for room_id in self._subscriptions.subscribed_room_ids():
            if ws.is_topic_joined(chat_room_topic(room_id)) and ws.is_topic_joined(
                room_participants_topic(room_id)
            ):
                continue
            if self._subscriptions.mark_room_rejoin_failed(room_id=room_id):
                self._mark_needing_reconciliation(
                    room_id, self._rooms_needing_reconciliation, ws
                )

    def _detect_agent_topic_rejoin_failures(self, ws: WebSocketClient) -> None:
        """Same rejoin-failure detection as ``_detect_room_rejoin_failures``,
        for the single-topic agent channels."""
        for topic in self._subscriptions.joined_agent_topics():
            if ws.is_topic_joined(topic):
                continue
            if self._subscriptions.mark_agent_topic_rejoin_failed(topic=topic):
                self._mark_needing_reconciliation(
                    topic, self._agent_topics_needing_reconciliation, ws
                )

    # --- Event handlers (from BandAgent, unified into PlatformEvent) ---

    async def _on_reconnected(self) -> None:
        """Handle PHX client reconnection.

        PHXChannelsClient re-subscribes previously joined topics before calling
        this hook, so room subscription tracking must stay intact here.
        RoomPresence can then reconcile tracked rooms against the server state
        without leaking channels or replaying duplicate room joins.

        Detects any room/agent topic that failed to *rejoin* (as opposed to
        the ambiguous-outcome cases already tracked locally) before draining,
        so a rejoin failure gets the same best-effort clean leave as every
        other ambiguous case in the same reconnect cycle.

        Also drains anything left in the local reconciliation sets: the
        transport's own auto-rejoin above can silently succeed for a room/
        topic whose prior join or leave was ambiguous (cancelled, or a
        failed rollback), so ``_drain_reconciliation`` forces a clean leave
        before acknowledging the tracker — this is the only point where that
        ambiguity is safely resolvable (see design doc).
        """
        logger.info("WebSocket reconnected — reconciling room state")
        self._subscriptions.on_reconnected()
        assert (
            self._ws is not None
        )  # only called from the ws client's own reconnect hook
        ws = self._ws
        self._detect_room_rejoin_failures(ws)
        self._detect_agent_topic_rejoin_failures(ws)
        await self._drain_reconciliation()
        self._queue_event(ReconnectedEvent())

    async def _drain_reconciliation(self) -> None:
        """Force a clean transport + tracker state for every room/topic left
        ambiguous since the last reconnect, then release the local block.
        """
        assert (
            self._ws is not None
        )  # only called from the ws client's own reconnect hook
        ws = self._ws

        await self._drain_room_reconciliation(ws)
        await self._drain_agent_topic_reconciliation(ws)

    async def _drain_room_reconciliation(self, ws: WebSocketClient) -> None:
        """``ws`` is the client captured at the start of the drain, not
        re-read from ``self._ws``: a concurrent disconnect()/reconnect() can
        swap or clear it while this is mid-await, and bailing out as soon as
        that's detected avoids both acting through a stale client (whose
        channels this session no longer owns) and further pointless work —
        never a correctness issue on its own, since every leave here is
        already best-effort, just needless.
        """
        for room_id in list(self._rooms_needing_reconciliation):
            if self._ws is not ws:
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
        """Same stale-``ws``-detection rationale as ``_drain_room_reconciliation``."""
        for topic in list(self._agent_topics_needing_reconciliation):
            if self._ws is not ws:
                return
            kind, _, agent_id = topic.partition(":")
            leave = (
                ws.leave_agent_rooms_channel
                if kind == _AGENT_ROOMS_KIND
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
        """
        Handle room_added from WebSocket.

        From BandAgent._on_room_added() lines 619-630.
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

        From BandAgent._on_room_removed() lines 632-643.
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

        From BandAgent._on_message_created() lines 645-682.
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

        From BandAgent._on_participant_added() lines 771-786.
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

        From BandAgent._on_participant_removed() lines 788-805.
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
