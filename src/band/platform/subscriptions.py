"""Room and agent-topic subscription tracking, join/leave orchestration, and
reconnect reconciliation for one BandLink -- no REST or event-queue state
involved.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from band.client.streaming import WebSocketClient
from band_sdk_core import (
    AgentTopicKind,
    LeaveOutcome,
    RoomSubscribeResult,
    SubscriptionTracker,
    chat_room_topic,
    room_participants_topic,
)

if TYPE_CHECKING:
    from band.client.streaming import (
        ContactAddedPayload,
        ContactRemovedPayload,
        ContactRequestReceivedPayload,
        ContactRequestUpdatedPayload,
        MessageCreatedPayload,
        ParticipantAddedPayload,
        ParticipantRemovedPayload,
        RoomAddedPayload,
        RoomDeletedPayload,
        RoomRemovedPayload,
    )

logger = logging.getLogger(__name__)


class SubscriptionManager:
    """Room/agent-topic subscription state for one ``BandLink``.

    ``ws`` is taken per call, not cached at construction: the caller
    (``BandLink``) owns the WebSocket client and may swap it at any point
    (a reconnect), so every call here uses whatever ``ws`` the caller
    currently passes rather than a snapshot from construction time.

    ``current_ws`` is the one exception -- a getter closure over
    ``BandLink._ws``, used only to detect a concurrent reconnect swapping
    the session out from under an in-flight operation (see
    ``_is_current_session``). It is never used to obtain a ``ws`` to act
    through; every operation still takes its own ``ws`` argument.
    """

    def __init__(self, current_ws: Callable[[], WebSocketClient | None]) -> None:
        self._current_ws = current_ws
        self._subscriptions = SubscriptionTracker()
        self._rooms_needing_reconciliation: set[str] = set()
        self._agent_topics_needing_reconciliation: set[str] = set()

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
        return self._current_ws() is ws

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

    async def subscribe_agent_rooms(
        self,
        ws: WebSocketClient,
        agent_id: str,
        *,
        on_room_added: Callable[[RoomAddedPayload], Awaitable[None]],
        on_room_removed: Callable[[RoomRemovedPayload], Awaitable[None]],
    ) -> None:
        """Subscribe to agent room events (room_added/removed)."""
        await self._subscribe_agent_topic(
            AgentTopicKind.Rooms.topic(agent_id),
            lambda: ws.join_agent_rooms_channel(
                agent_id,
                on_room_added=on_room_added,
                on_room_removed=on_room_removed,
            ),
            ws,
        )

    async def subscribe_room(
        self,
        ws: WebSocketClient,
        room_id: str,
        *,
        on_message_created: Callable[[MessageCreatedPayload], Awaitable[None]],
        on_participant_added: Callable[[ParticipantAddedPayload], Awaitable[None]],
        on_participant_removed: Callable[[ParticipantRemovedPayload], Awaitable[None]],
        on_room_deleted: Callable[[RoomDeletedPayload], Awaitable[None]],
    ) -> None:
        """Subscribe to room messages and participants.

        Blocked (a no-op, logged) while ``room_id`` is in
        ``_rooms_needing_reconciliation`` — a prior cancelled/ambiguous
        attempt's outcome is unresolved and must not be retried on the same
        socket. Stays blocked until the next reconnect drains it (see
        ``drain_reconciliation``); see the design doc for why.
        """
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
                    on_message_created=on_message_created,
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
                    on_participant_added=on_participant_added,
                    on_participant_removed=on_participant_removed,
                    on_room_deleted=on_room_deleted,
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

    async def subscribe_agent_contacts(
        self,
        ws: WebSocketClient,
        agent_id: str,
        *,
        on_contact_request_received: Callable[
            [ContactRequestReceivedPayload], Awaitable[None]
        ],
        on_contact_request_updated: Callable[
            [ContactRequestUpdatedPayload], Awaitable[None]
        ],
        on_contact_added: Callable[[ContactAddedPayload], Awaitable[None]],
        on_contact_removed: Callable[[ContactRemovedPayload], Awaitable[None]],
    ) -> None:
        """
        Subscribe to agent contact events.

        Events: contact_request_received, contact_request_updated,
                contact_added, contact_removed
        """
        await self._subscribe_agent_topic(
            AgentTopicKind.Contacts.topic(agent_id),
            lambda: ws.join_agent_contacts_channel(
                agent_id,
                on_contact_request_received=on_contact_request_received,
                on_contact_request_updated=on_contact_request_updated,
                on_contact_added=on_contact_added,
                on_contact_removed=on_contact_removed,
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

    async def unsubscribe_room(self, ws: WebSocketClient, room_id: str) -> None:
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

    async def unsubscribe_agent_contacts(
        self, ws: WebSocketClient, agent_id: str
    ) -> None:
        """Unsubscribe from agent contacts channel.

        A true no-op when the topic was never joined — the tracker's
        ``leave_agent_topic`` returns ``None`` in that case rather than
        issuing a leave the transport would just reject.
        """
        await self._leave_agent_topic(
            AgentTopicKind.Contacts.topic(agent_id),
            lambda: ws.leave_agent_contacts_channel(agent_id),
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
        this whole reconnect pass by ``BandLink._on_reconnected`` -- never
        taken here directly, so all detection in the pass sees the same
        registry state.
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

    def mark_reconnected(self) -> None:
        self._subscriptions.on_reconnected()

    def detect_rejoin_failures(
        self, ws: WebSocketClient, joined: frozenset[str]
    ) -> None:
        self._detect_room_rejoin_failures(ws, joined)
        self._detect_agent_topic_rejoin_failures(ws, joined)

    async def drain_reconciliation(self, ws: WebSocketClient) -> None:
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

    def end_session(self) -> None:
        self._subscriptions.end_session()
        self._rooms_needing_reconciliation.clear()
        self._agent_topics_needing_reconciliation.clear()
