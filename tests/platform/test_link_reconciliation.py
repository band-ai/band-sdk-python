"""BandLinkSubscriptionRaceAndReconciliation behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import create_autospec, patch

from band_sdk_core import AgentTopicStatus, chat_room_topic

from band.client.streaming import (
    WebSocketClient,
)
from band.platform.link import BandLink
from tests.platform.conftest import (
    AllTopicsExcept,
    AllTopicsJoined,
    cancelled_mid_await,
)


class TestBandLinkSubscriptionRaceAndReconciliation:
    """SubscriptionTracker-backed dedup, reconciliation blocking, and
    cancellation safety for subscribe_room/unsubscribe_room."""

    @patch("band.platform.link.WebSocketClient")
    async def test_concurrent_subscribe_room_only_one_join(
        self, mock_ws_class, mock_ws_client
    ):
        """Two concurrent subscribe_room() calls for the same room must not
        both attempt the wire join: begin_room_subscribe's pre-await claim
        check makes the loser a synchronous no-op rather than a second
        `join_chat_room_channel` call."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await asyncio.gather(
            link.subscribe_room("room-123"),
            link.subscribe_room("room-123"),
        )

        assert mock_ws_client.join_chat_room_channel.call_count == 1
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_first_join_failure_is_not_blocking(
        self, mock_ws_class, mock_ws_client
    ):
        """A failure on the *first* join (chat_room) has no rollback to be
        ambiguous about — record_chat_room_join_failed resolves it cleanly,
        unlike the second-join-plus-failed-rollback case, so a retry must
        succeed immediately with no reconnect needed."""
        mock_ws_class.return_value = mock_ws_client
        mock_ws_client.join_chat_room_channel.side_effect = Exception(
            "chat_room join failed"
        )

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is False

        mock_ws_client.join_chat_room_channel.side_effect = None
        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_drain_reconciliation_bails_on_ws_swap_mid_drain(
        self, mock_ws_class, mock_ws_client
    ):
        """A concurrent disconnect()/connect() completing while
        _drain_reconciliation() is mid-loop must stop it from acting through
        the now-stale ``ws`` it captured at the start. The staleness check
        only runs at the top of each loop iteration (never mid-room), so
        whichever room is first in flight when the swap happens still
        completes its own pair of leave calls — the guarantee is that the
        *other* room, and the agent-topic drain that runs after, are never
        touched. Room iteration order is a plain ``set`` (unordered), so the
        swap fires unconditionally on the first ``leave_chat_room_channel``
        call and assertions are made by count, not by which literal room id
        went first."""
        mock_ws_class.return_value = mock_ws_client
        other_ws = create_autospec(WebSocketClient, instance=True)

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        link._subscriptions_manager._rooms_needing_reconciliation.update(
            {"room-1", "room-2"}
        )
        link._subscriptions_manager._agent_topics_needing_reconciliation.add(
            "agent_rooms:agent-123"
        )

        def swap_ws_mid_leave(room_id: str) -> None:
            link._ws = other_ws

        mock_ws_client.leave_chat_room_channel.side_effect = swap_ws_mid_leave

        await link._drain_reconciliation()

        # The old operation cannot acknowledge pending entries after replacement.
        assert mock_ws_client.leave_chat_room_channel.call_count == 1
        assert mock_ws_client.leave_room_participants_channel.call_count == 1
        assert link._subscriptions_manager._rooms_needing_reconciliation == {
            "room-1",
            "room-2",
        }

        # The agent-topic drain runs next and finds a stale `ws` right away —
        # it never touches the topic at all.
        mock_ws_client.leave_agent_rooms_channel.assert_not_called()
        other_ws.leave_agent_rooms_channel.assert_not_called()
        assert link._subscriptions_manager._agent_topics_needing_reconciliation == {
            "agent_rooms:agent-123"
        }

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_blocked_after_failed_rollback_until_reconnect(
        self, mock_ws_class, mock_ws_client
    ):
        """A room whose rollback also failed (both topics ambiguous
        server-side) must not be resubscribed on the same socket — it stays
        blocked until the next reconnect drains the reconciliation set."""
        mock_ws_class.return_value = mock_ws_client
        mock_ws_client.join_room_participants_channel.side_effect = Exception(
            "participants join failed"
        )
        mock_ws_client.leave_chat_room_channel.side_effect = Exception(
            "rollback leave also failed"
        )

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is False

        # Blocked: a retry before the next reconnect must not attempt a join.
        mock_ws_client.join_chat_room_channel.reset_mock()
        await link.subscribe_room("room-123")
        mock_ws_client.join_chat_room_channel.assert_not_called()

        # The next reconnect drains the reconciliation set, unblocking it.
        mock_ws_client.join_room_participants_channel.side_effect = None
        mock_ws_client.leave_chat_room_channel.side_effect = None
        mock_ws_client.joined_topics.return_value = frozenset(
            {"agent_control:agent-123"}
        )
        await link._on_reconnected()

        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_reconnect_issues_best_effort_leave_before_acknowledging(
        self, mock_ws_class, mock_ws_client
    ):
        """_on_reconnected() must force a clean transport leave for every
        room still needing reconciliation before acknowledging the tracker —
        closes the gap where Client's own auto-rejoin can
        silently re-establish a stale-registered topic ahead of this hook."""
        mock_ws_class.return_value = mock_ws_client
        mock_ws_client.join_room_participants_channel.side_effect = Exception("boom")
        mock_ws_client.leave_chat_room_channel.side_effect = Exception(
            "rollback also failed"
        )

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        mock_ws_client.leave_chat_room_channel.side_effect = None
        mock_ws_client.leave_chat_room_channel.reset_mock()
        mock_ws_client.leave_room_participants_channel.reset_mock()

        mock_ws_client.joined_topics.return_value = frozenset(
            {"agent_control:agent-123"}
        )
        await link._on_reconnected()

        mock_ws_client.leave_chat_room_channel.assert_called_once_with("room-123")
        mock_ws_client.leave_room_participants_channel.assert_called_once_with(
            "room-123"
        )

    @patch("band.platform.link.WebSocketClient")
    async def test_room_rejoin_failure_is_detected_and_drained_on_reconnect(
        self, mock_ws_class, mock_ws_client
    ):
        """A room whose chat_room topic didn't survive PHX's own rejoin pass
        is caught by _detect_room_rejoin_failures: the candidate ticket it
        reports still matches the tracker's current generation, so the
        report applies. Detection runs before _drain_reconciliation within
        the same _on_reconnected() call, so the room is force-left and
        acknowledged immediately -- no extra reconnect needed before a fresh
        subscribe succeeds."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

        mock_ws_client.joined_topics.side_effect = [
            AllTopicsExcept({chat_room_topic("room-123")}),
            frozenset({"agent_control:agent-123"}),
        ]
        await link._on_reconnected()
        assert link.is_room_subscribed("room-123") is False
        mock_ws_client.leave_chat_room_channel.assert_called_once_with("room-123")
        mock_ws_client.leave_room_participants_channel.assert_called_once_with(
            "room-123"
        )

        mock_ws_client.joined_topics.side_effect = None
        mock_ws_client.joined_topics.return_value = AllTopicsJoined()
        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_agent_topic_rejoin_failure_is_detected_and_drained_on_reconnect(
        self, mock_ws_class, mock_ws_client
    ):
        """Same detection for the single-topic agent_rooms channel:
        _detect_agent_topic_rejoin_failures reports the current ticket, the
        tracker applies it, and the same-call drain force-leaves and
        acknowledges it immediately."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_rooms("agent-123")

        topic = "agent_rooms:agent-123"
        mock_ws_client.joined_topics.return_value = AllTopicsExcept({topic})
        await link._on_reconnected()
        mock_ws_client.leave_agent_rooms_channel.assert_called_once_with("agent-123")
        assert (
            link._subscriptions_manager._subscriptions.agent_topic_status(topic)
            == AgentTopicStatus.Absent
        )

        mock_ws_client.joined_topics.side_effect = None
        mock_ws_client.joined_topics.return_value = AllTopicsJoined()
        mock_ws_client.join_agent_rooms_channel.reset_mock()
        await link.subscribe_agent_rooms("agent-123")
        mock_ws_client.join_agent_rooms_channel.assert_called_once()

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_cancelled_mid_join_blocks_until_reconnect(
        self, mock_ws_class, mock_ws_client
    ):
        """Cancellation mid-await must resolve the ticket (never leak it)
        and block the room until the next reconnect — proven with a gated
        coroutine so the cancel lands truly mid-flight, not before entry."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        async with cancelled_mid_await(
            mock_ws_client.join_chat_room_channel, link.subscribe_room("room-123")
        ):
            pass

        assert link.is_room_subscribed("room-123") is False

        mock_ws_client.join_chat_room_channel.side_effect = None
        mock_ws_client.join_chat_room_channel.reset_mock()
        await link.subscribe_room("room-123")
        mock_ws_client.join_chat_room_channel.assert_not_called()

        mock_ws_client.joined_topics.return_value = frozenset(
            {"agent_control:agent-123"}
        )
        await link._on_reconnected()

        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_cancelled_mid_leave_blocks_until_reconnect(
        self, mock_ws_class, mock_ws_client
    ):
        """Same guarantee on the leave path: a cancelled unsubscribe_room()
        resolves to LeaveOutcome.Unknown and blocks the room, never leaking
        the ticket."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        async with cancelled_mid_await(
            mock_ws_client.leave_chat_room_channel, link.unsubscribe_room("room-123")
        ):
            pass

        assert link.is_room_subscribed("room-123") is False

        mock_ws_client.join_chat_room_channel.reset_mock()
        await link.subscribe_room("room-123")
        mock_ws_client.join_chat_room_channel.assert_not_called()

        mock_ws_client.joined_topics.return_value = frozenset(
            {"agent_control:agent-123"}
        )
        await link._on_reconnected()

        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_disconnect_during_cancelled_subscribe_does_not_block_next_session(
        self, mock_ws_class, mock_ws_client
    ):
        """A subscribe_room() cancelled after disconnect() already tore
        down the session must not leave a reconciliation entry that blocks
        the room on a later, unrelated connection — the block only belongs
        to the session that produced the ambiguity, never one that outlives
        it."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        async with cancelled_mid_await(
            mock_ws_client.join_chat_room_channel, link.subscribe_room("room-123")
        ):
            await link.disconnect()

        # A fresh connection is unrelated to the torn-down session's
        # ambiguity — subscribing must actually attempt the join, not
        # silently no-op as if still blocked.
        mock_ws_client.join_chat_room_channel.side_effect = None
        mock_ws_client.join_chat_room_channel.reset_mock()
        await link.connect()
        await link.subscribe_room("room-123")

        mock_ws_client.join_chat_room_channel.assert_called_once()
        assert link.is_room_subscribed("room-123") is True
