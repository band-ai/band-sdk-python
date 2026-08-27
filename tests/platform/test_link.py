"""Tests for BandLink."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from band_rest import (
    AsyncRestClient,
    NotFoundError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from band_rest.core.api_error import ApiError

from band.client.streaming import (
    MessageCreatedPayload,
    MessageMetadata,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    SupersedePayload,
    WebSocketClient,
)
from band.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
    WebSocketDisconnectedEvent,
)
from band.platform.link import BandLink

from tests.conftest import make_message_event
from tests.platform.conftest import cancelled_mid_await


@pytest.fixture
def mock_ws_client():
    """Autospecced WebSocketClient for testing BandLink.

    ``spec=WebSocketClient`` (via ``create_autospec``) so a rename/removal of
    a real method surfaces as a test failure here, instead of the mock
    silently auto-fabricating whatever attribute BandLink happens to call.
    """
    ws = create_autospec(WebSocketClient, instance=True)

    # Async context manager support
    ws.__aenter__.return_value = ws
    ws.__aexit__.return_value = None

    ws.last_disconnect_reason = None

    def record_terminal_disconnect(reason):
        ws.last_disconnect_reason = reason

    ws.record_terminal_disconnect.side_effect = record_terminal_disconnect

    return ws


class TestBandLinkConstruction:
    """Test BandLink initialization."""

    def test_init_stores_credentials(self):
        """Should store agent_id, api_key, and URLs."""
        link = BandLink(
            agent_id="agent-123",
            api_key="test-key",
            ws_url="wss://test.com/ws",
            rest_url="https://test.com",
        )

        assert link.agent_id == "agent-123"
        assert link.api_key == "test-key"
        assert link.ws_url == "wss://test.com/ws"
        assert link.rest_url == "https://test.com"

    def test_init_creates_rest_client(self):
        """Should create AsyncRestClient exposed as .rest."""
        link = BandLink(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert link.rest is not None

    def test_init_starts_disconnected(self):
        """Should start in disconnected state."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        assert link.is_connected is False
        assert link._ws is None
        assert link.is_room_subscribed("room-123") is False

    def test_init_empty_event_queue(self):
        """Should start with empty event queue."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        assert link._event_queue.empty()


class TestBandLinkConnection:
    """Test connection lifecycle."""

    @patch("band.platform.link.WebSocketClient")
    async def test_connect_creates_websocket(self, mock_ws_class, mock_ws_client):
        """connect() should create WebSocketClient and enter context."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        mock_ws_class.assert_called_once_with(
            link.ws_url,
            link.api_key,
            link.agent_id,
            on_reconnect=link._on_reconnected,
            on_disconnect=link._on_disconnected,
        )
        mock_ws_client.__aenter__.assert_called_once()
        mock_ws_client.join_agent_control_channel.assert_called_once_with(
            link.agent_id,
            on_supersede=link._on_supersede,
            on_control=link._on_control,
        )
        assert link.is_connected is True

    @patch("band.platform.link.WebSocketClient")
    async def test_connect_when_already_connected_logs_warning(
        self, mock_ws_class, mock_ws_client
    ):
        """connect() when already connected should log warning and return."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.connect()  # Second call

        # Should only create WS once
        assert mock_ws_class.call_count == 1

    @patch("band.platform.link.WebSocketClient")
    async def test_concurrent_connect_only_creates_one_websocket(
        self, mock_ws_class, mock_ws_client
    ):
        """Two genuinely concurrent connect() calls must not both build a
        WebSocketClient: the guard has to be the synchronous `self._ws`
        assignment itself, not `self._is_connected` (only set true after
        two awaits) — otherwise the second call races past the flag and
        leaks the first client."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")

        await asyncio.gather(link.connect(), link.connect())

        assert mock_ws_class.call_count == 1
        assert link.is_connected is True

    @patch("band.platform.link.WebSocketClient")
    async def test_cancelled_connect_clears_ws_for_a_later_retry(
        self, mock_ws_class, mock_ws_client
    ):
        """A connect() cancelled while awaiting __aenter__() must roll _ws
        back to None -- otherwise every later connect() sees a non-None _ws
        and silently no-ops forever, with is_connected stuck False."""
        mock_ws_class.return_value = mock_ws_client
        link = BandLink(agent_id="agent-123", api_key="test-key")

        async with cancelled_mid_await(mock_ws_client.__aenter__, link.connect()):
            pass

        assert link._ws is None
        assert link.is_connected is False
        mock_ws_client.__aexit__.assert_called_once()

        await link.connect()

        assert link.is_connected is True

    @patch("band.platform.link.WebSocketClient")
    async def test_disconnect_exits_websocket_context(
        self, mock_ws_class, mock_ws_client
    ):
        """disconnect() should exit WebSocket context."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.disconnect()

        mock_ws_client.__aexit__.assert_called_once_with(None, None, None)
        assert link.is_connected is False
        assert link._ws is None

    @patch("band.platform.link.WebSocketClient")
    async def test_disconnect_clears_subscribed_rooms(
        self, mock_ws_class, mock_ws_client
    ):
        """disconnect() should clear tracked subscriptions."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-1")
        await link.subscribe_room("room-2")

        await link.disconnect()

        assert link.is_room_subscribed("room-1") is False
        assert link.is_room_subscribed("room-2") is False

    @patch("band.platform.link.WebSocketClient")
    async def test_reconnect_keeps_tracked_room_subscriptions(
        self, mock_ws_class, mock_ws_client
    ):
        """_on_reconnected() should preserve room tracking for PHX
        re-subscriptions — a normally-subscribed room needs no reconciliation
        leave, so the drain must leave it untouched."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-1")
        await link.subscribe_room("room-2")

        await link._on_reconnected()

        assert link.is_room_subscribed("room-1") is True
        assert link.is_room_subscribed("room-2") is True
        mock_ws_client.leave_chat_room_channel.assert_not_called()
        mock_ws_client.leave_room_participants_channel.assert_not_called()

    async def test_disconnect_when_not_connected_is_noop(self):
        """disconnect() when not connected should be a no-op."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.disconnect()  # Should not raise

        assert link.is_connected is False

    @patch("band.platform.link.WebSocketClient")
    async def test_run_forever_delegates_to_websocket(
        self, mock_ws_class, mock_ws_client
    ):
        """run_forever() should delegate to WebSocket."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.run_forever()

        mock_ws_client.run_forever.assert_called_once()

    async def test_run_forever_raises_when_not_connected(self):
        """run_forever() should raise RuntimeError when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.run_forever()

    async def test_supersede_records_terminal_reason_and_queues_event(
        self, mock_ws_client
    ):
        """supersede records the platform reason and disables reconnect before close."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link._ws = mock_ws_client
        link._is_connected = True
        payload = SupersedePayload(
            reason="session.already_connected",
            message="This connection has been superseded by a newer session for this agent.",
            retryable=False,
            retry_after=15,
            target_socket_id="agent_socket:agent-123",
            correlation_id="evict-123",
        )

        await link._on_supersede(payload)

        mock_ws_client.record_terminal_disconnect.assert_called_once_with(
            link.last_disconnect_reason
        )
        assert link.is_connected is False
        assert link.last_disconnect_reason is not None
        assert link.last_disconnect_reason.reason == "session.already_connected"
        event = await link.__anext__()
        assert isinstance(event, WebSocketDisconnectedEvent)
        assert event.payload == link.last_disconnect_reason

    async def test_disconnect_after_supersede_still_cleans_up_websocket(
        self, mock_ws_client
    ):
        """disconnect() should clean up the websocket even after terminal state flips."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link._ws = mock_ws_client
        link._is_connected = True
        await link.subscribe_room("room-1")
        payload = SupersedePayload(
            reason="session.already_connected",
            message="This connection has been superseded by a newer session for this agent.",
            retryable=False,
            retry_after=15,
            target_socket_id="agent_socket:agent-123",
            correlation_id="evict-123",
        )

        await link._on_supersede(payload)
        await link.disconnect()

        mock_ws_client.__aexit__.assert_called_once_with(None, None, None)
        assert link.is_connected is False
        assert link._ws is None
        assert link.is_room_subscribed("room-1") is False
        assert link.last_disconnect_reason is not None
        assert link.last_disconnect_reason.reason == "session.already_connected"

    async def test_close_without_supersede_leaves_disconnect_reason_empty(self):
        """An empty Phoenix close should not invent a terminal reason."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        await link._on_disconnected(None)

        assert link.last_disconnect_reason is None
        assert link._event_queue.empty()


class TestBandLinkSubscriptions:
    """Test subscription management."""

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_agent_rooms_joins_channel(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_agent_rooms() should join agent rooms channel."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_rooms("agent-123")

        mock_ws_client.join_agent_rooms_channel.assert_called_once()
        # Verify callbacks were passed
        call_kwargs = mock_ws_client.join_agent_rooms_channel.call_args[1]
        assert "on_room_added" in call_kwargs
        assert "on_room_removed" in call_kwargs

    async def test_subscribe_agent_rooms_raises_when_not_connected(self):
        """subscribe_agent_rooms() should raise when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_agent_rooms("agent-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_joins_channels(self, mock_ws_class, mock_ws_client):
        """subscribe_room() should join chat room and participants channels."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        mock_ws_client.join_chat_room_channel.assert_called_once()
        mock_ws_client.join_room_participants_channel.assert_called_once()

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_tracks_subscription(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_room() should track the room as subscribed."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_idempotent(self, mock_ws_class, mock_ws_client):
        """subscribe_room() twice should not re-subscribe."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.subscribe_room("room-123")  # Second call

        # Should only join once
        assert mock_ws_client.join_chat_room_channel.call_count == 1

    async def test_subscribe_room_raises_when_not_connected(self):
        """subscribe_room() should raise when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_room("room-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_leaves_channels(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should leave both channels."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.unsubscribe_room("room-123")

        mock_ws_client.leave_chat_room_channel.assert_called_once_with("room-123")
        mock_ws_client.leave_room_participants_channel.assert_called_once_with(
            "room-123"
        )

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_removes_from_tracking(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should remove the room from tracking."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.unsubscribe_room("room-123")

        assert link.is_room_subscribed("room-123") is False

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_handles_leave_errors(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should handle errors gracefully."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        mock_ws_client.leave_chat_room_channel.side_effect = Exception("Leave failed")

        # Should not raise, just log warning
        await link.unsubscribe_room("room-123")

        # Room should still be removed from tracking despite the leave failure
        assert link.is_room_subscribed("room-123") is False

    async def test_unsubscribe_room_noop_when_not_subscribed(self):
        """unsubscribe_room() should be no-op for unsubscribed room."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        # Should not raise
        await link.unsubscribe_room("room-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_noop_when_connected_but_never_subscribed(
        self, mock_ws_class, mock_ws_client
    ):
        """A connected link with no prior subscribe_room() call must hit the
        tracker's own ``ticket is None`` no-op — distinct from the
        not-connected case above, which short-circuits earlier on ``self._ws``
        and never reaches the tracker at all."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await link.unsubscribe_room("room-123")

        mock_ws_client.leave_chat_room_channel.assert_not_called()
        mock_ws_client.leave_room_participants_channel.assert_not_called()


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
        link._rooms_needing_reconciliation.update({"room-1", "room-2"})
        link._agent_topics_needing_reconciliation.add("agent_rooms:agent-123")

        def swap_ws_mid_leave(room_id: str) -> None:
            link._ws = other_ws

        mock_ws_client.leave_chat_room_channel.side_effect = swap_ws_mid_leave

        await link._drain_reconciliation()

        # Only the room in flight when the swap happened got its pair of
        # leave calls (and its own acknowledge/discard); the other was never
        # reached once the staleness check caught the swap.
        assert mock_ws_client.leave_chat_room_channel.call_count == 1
        assert mock_ws_client.leave_room_participants_channel.call_count == 1
        assert len(link._rooms_needing_reconciliation) == 1

        # The agent-topic drain runs next and finds a stale `ws` right away —
        # it never touches the topic at all.
        mock_ws_client.leave_agent_rooms_channel.assert_not_called()
        other_ws.leave_agent_rooms_channel.assert_not_called()
        assert link._agent_topics_needing_reconciliation == {"agent_rooms:agent-123"}

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
        await link._on_reconnected()

        await link.subscribe_room("room-123")
        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_reconnect_issues_best_effort_leave_before_acknowledging(
        self, mock_ws_class, mock_ws_client
    ):
        """_on_reconnected() must force a clean transport leave for every
        room still needing reconciliation before acknowledging the tracker —
        closes the gap where PHXChannelsClient's own auto-rejoin can
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

        await link._on_reconnected()

        mock_ws_client.leave_chat_room_channel.assert_called_once_with("room-123")
        mock_ws_client.leave_room_participants_channel.assert_called_once_with(
            "room-123"
        )

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


class TestBandLinkEventQueue:
    """Test event queue mechanism (async iterator pattern)."""

    def test_queue_event_adds_to_queue(self):
        """_queue_event() should add event to queue."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        link._queue_event(event)

        assert link._event_queue.qsize() == 1

    async def test_async_iteration_gets_events(self):
        """async for should yield events from queue."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        link._queue_event(event)

        # Get event via async iteration
        received = await link.__anext__()
        assert received is event

    def test_queue_drops_when_full(self):
        """Queue should drop events when full (no blocking)."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        # Fill the queue (maxsize=1000)
        for i in range(1000):
            link._queue_event(make_message_event(msg_id=f"msg-{i}"))

        # Queue should be full
        assert link._event_queue.full()

        # Adding one more should not block (drops or handles gracefully)
        # Note: Exact behavior depends on implementation


class TestBandLinkEventHandlers:
    """Test internal event handlers that queue typed events."""

    async def test_on_room_added_queues_room_added_event(self):
        """_on_room_added() should queue RoomAddedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = RoomAddedPayload(
            id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            title="Test Room",
        )

        await link._on_room_added(payload)

        # Check event was queued
        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomAddedEvent)
        assert event.room_id == "room-123"

    async def test_on_room_removed_queues_room_removed_event(self):
        """_on_room_removed() should queue RoomRemovedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = RoomRemovedPayload(
            id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            title="Test Room",
        )

        await link._on_room_removed(payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomRemovedEvent)
        assert event.room_id == "room-123"

    async def test_on_message_created_queues_message_event(self):
        """_on_message_created() should queue MessageEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = MessageCreatedPayload(
            id="msg-123",
            content="Hello",
            message_type="text",
            sender_id="user-456",
            sender_type="User",
            chat_room_id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            metadata=MessageMetadata(mentions=[], status="sent"),
        )

        await link._on_message_created("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, MessageEvent)
        assert event.room_id == "room-123"
        assert event.payload.content == "Hello"

    async def test_on_participant_added_queues_participant_added_event(self):
        """_on_participant_added() should queue ParticipantAddedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = ParticipantAddedPayload(id="user-123", name="Test User", type="User")

        await link._on_participant_added("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, ParticipantAddedEvent)
        assert event.room_id == "room-123"
        assert event.payload.id == "user-123"

    async def test_on_participant_removed_queues_participant_removed_event(self):
        """_on_participant_removed() should queue ParticipantRemovedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = ParticipantRemovedPayload(
            id="user-123", name="Test User", type="User"
        )

        await link._on_participant_removed("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, ParticipantRemovedEvent)
        assert event.room_id == "room-123"

    async def test_on_room_deleted_queues_room_deleted_event(self):
        """_on_room_deleted() should queue RoomDeletedEvent."""

        link = BandLink(agent_id="agent-123", api_key="test-key")

        payload = RoomDeletedPayload(id="room-123")

        await link._on_room_deleted("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomDeletedEvent)
        assert event.room_id == "room-123"
        assert event.payload.id == "room-123"


class TestMessageLifecycleMarks:
    """Tests for message lifecycle status return values."""

    @pytest.mark.asyncio
    async def test_mark_processing_returns_true_on_success(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_processing = AsyncMock()

        result = await link.mark_processing("room-1", "msg-1")

        assert result is True
        link.rest.agent_api_messages.mark_agent_message_processing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_processing_returns_false_on_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_processing = AsyncMock(
            side_effect=Exception("network down")
        )

        result = await link.mark_processing("room-1", "msg-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_processed_returns_true_on_success(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_processed = AsyncMock()

        result = await link.mark_processed("room-1", "msg-1")

        assert result is True
        link.rest.agent_api_messages.mark_agent_message_processed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_processed_returns_false_on_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_processed = AsyncMock(
            side_effect=Exception("network down")
        )

        result = await link.mark_processed("room-1", "msg-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_failed_returns_true_on_success(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        result = await link.mark_failed("room-1", "msg-1", "boom")

        assert result is True
        link.rest.agent_api_messages.mark_agent_message_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_failed_returns_false_on_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock(
            side_effect=Exception("network down")
        )

        result = await link.mark_failed("room-1", "msg-1", "boom")

        assert result is False


class TestMarkFailed:
    """Tests for mark_failed error normalization."""

    @pytest.mark.asyncio
    async def test_replaces_empty_error_with_unknown(self):
        """mark_failed should replace empty error string with 'Unknown error'."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        await link.mark_failed("room-1", "msg-1", "")

        link.rest.agent_api_messages.mark_agent_message_failed.assert_called_once()
        call_kwargs = link.rest.agent_api_messages.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "Unknown error"

    @pytest.mark.asyncio
    async def test_replaces_whitespace_error_with_unknown(self):
        """mark_failed should replace whitespace-only error with 'Unknown error'."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        await link.mark_failed("room-1", "msg-1", "   ")

        call_kwargs = link.rest.agent_api_messages.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "Unknown error"

    @pytest.mark.asyncio
    async def test_passes_through_non_empty_error(self):
        """mark_failed should pass through a valid error string as-is."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        await link.mark_failed("room-1", "msg-1", "connection reset")

        call_kwargs = link.rest.agent_api_messages.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "connection reset"


class TestGetNextMessage:
    """Tests for the /next REST wrapper."""

    @pytest.mark.asyncio
    async def test_returns_none_on_204(self) -> None:
        """204 No Content is the platform's "no actionable message" signal —
        the only ``ApiError`` that should resolve to ``None``."""

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.get_agent_next_message = AsyncMock(
            side_effect=ApiError(status_code=204, body=None)
        )

        assert await link.get_next_message("room-1") is None

    @pytest.mark.asyncio
    async def test_raises_on_non_204_api_error(self) -> None:
        """Regression: a 5xx or other API failure must propagate so callers
        can distinguish "no pending" from "lookup failed." The old behavior
        swallowed both as ``None``, which silently dropped messages at the
        OneShot claim step."""

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.get_agent_next_message = AsyncMock(
            side_effect=ApiError(status_code=503, body="upstream down")
        )

        with pytest.raises(ApiError):
            await link.get_next_message("room-1")

    @pytest.mark.asyncio
    async def test_raises_on_transport_error(self) -> None:
        """Connection errors / timeouts also propagate — same reason."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.get_agent_next_message = AsyncMock(
            side_effect=ConnectionError("dns failure")
        )

        with pytest.raises(ConnectionError):
            await link.get_next_message("room-1")

    @pytest.mark.asyncio
    async def test_returns_platform_message_on_success(self) -> None:
        """The happy path: a real response body is projected into a
        PlatformMessage — no test elsewhere in the suite exercises this
        construction, only the error/edge branches around it."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        item = MagicMock(
            id="msg-1",
            chat_room_id="room-1",
            content="hello",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={"mentions": []},
            inserted_at=None,
        )
        link.rest.agent_api_messages.get_agent_next_message = AsyncMock(
            return_value=MagicMock(data=item)
        )

        message = await link.get_next_message("room-1")

        assert message is not None
        assert message.id == "msg-1"
        assert message.room_id == "room-1"
        assert message.content == "hello"
        assert message.sender_name == "User One"

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response_body(self) -> None:
        """A 2xx with no ``data`` (server bug or an edge-case empty body) is
        treated the same as "nothing pending" — not a crash."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        response = MagicMock(data=None)
        link.rest.agent_api_messages.get_agent_next_message = AsyncMock(
            return_value=response
        )

        assert await link.get_next_message("room-1") is None


def make_stale_message(
    *, id: str, room_id: str, content: str, sender_id: str, sender_name: str
) -> MagicMock:
    """A fake REST `processing`-status message item, shaped like the
    band_rest SDK's response model just enough for
    ``get_stale_processing_messages`` to project it into a ``PlatformMessage``."""
    msg = MagicMock()
    msg.id = id
    msg.chat_room_id = room_id
    msg.content = content
    msg.sender_id = sender_id
    msg.sender_type = "User"
    msg.sender_name = sender_name
    msg.message_type = "text"
    msg.metadata = {}
    msg.inserted_at = None
    return msg


class TestGetStaleProcessingMessages:
    """Tests for stale processing recovery pagination."""

    @pytest.mark.asyncio
    async def test_paginates_across_all_pages(self):
        """get_stale_processing_messages should fetch every result page."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()

        msg_1 = make_stale_message(
            id="msg-1",
            room_id="room-1",
            content="first",
            sender_id="user-1",
            sender_name="User One",
        )
        msg_2 = make_stale_message(
            id="msg-2",
            room_id="room-1",
            content="second",
            sender_id="user-2",
            sender_name="User Two",
        )

        response_page_1 = MagicMock()
        response_page_1.data = [msg_1]
        response_page_1.metadata = MagicMock(page=1, total_pages=2)

        response_page_2 = MagicMock()
        response_page_2.data = [msg_2]
        response_page_2.metadata = MagicMock(page=2, total_pages=2)

        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            side_effect=[response_page_1, response_page_2]
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert [message.id for message in messages] == ["msg-1", "msg-2"]
        assert link.rest.agent_api_messages.list_agent_messages.await_count == 2
        first_call = link.rest.agent_api_messages.list_agent_messages.await_args_list[0]
        second_call = link.rest.agent_api_messages.list_agent_messages.await_args_list[
            1
        ]
        assert first_call.kwargs["page"] == 1
        assert second_call.kwargs["page"] == 2

    @pytest.mark.asyncio
    async def test_stops_after_first_page_when_total_pages_missing(self):
        """Missing pagination metadata should safely return the first page."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()

        msg = make_stale_message(
            id="msg-1",
            room_id="room-1",
            content="first",
            sender_id="user-1",
            sender_name="User One",
        )

        response_page_1 = MagicMock()
        response_page_1.data = [msg]
        response_page_1.metadata = MagicMock(total_pages=None)

        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            return_value=response_page_1
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert [message.id for message in messages] == ["msg-1"]
        link.rest.agent_api_messages.list_agent_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_failure(self):
        """This is a best-effort startup recovery sweep: a REST failure
        (mid-pagination or otherwise) must not crash agent startup — it
        returns an empty list instead of raising, unlike get_next_message's
        propagate-on-failure contract above."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            side_effect=Exception("network down")
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert messages == []


class TestReportActivity:
    """Tests for BandLink.report_activity (boolean working-state reporting)."""

    @pytest.mark.asyncio
    async def test_reports_working_true(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock()

        result = await link.report_activity("room-1", True)

        assert result is True
        call = link.rest.agent_api_activity.report_agent_chat_activity
        call.assert_awaited_once()
        assert call.call_args.kwargs["chat_id"] == "room-1"
        assert call.call_args.kwargs["working"] is True

    @pytest.mark.asyncio
    async def test_passes_per_post_timeout_and_no_retries(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock()

        await link.report_activity("room-1", True, timeout_seconds=2)

        opts = link.rest.agent_api_activity.report_agent_chat_activity.call_args.kwargs[
            "request_options"
        ]
        assert opts["timeout_in_seconds"] == 2
        assert opts["max_retries"] == 0

    @pytest.mark.asyncio
    async def test_reports_working_false(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock()

        result = await link.report_activity("room-1", False)

        assert result is True
        call = link.rest.agent_api_activity.report_agent_chat_activity
        assert call.call_args.kwargs["working"] is False

    @pytest.mark.asyncio
    async def test_returns_false_on_not_found(self):

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock(
            side_effect=NotFoundError(headers={}, body="no active execution")
        )

        result = await link.report_activity("room-1", True)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_unauthorized(self):

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock(
            side_effect=UnauthorizedError(headers={}, body="bad key")
        )

        result = await link.report_activity("room-1", True)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_unprocessable_entity(self):

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock(
            side_effect=UnprocessableEntityError(headers={}, body="bad uuid")
        )

        result = await link.report_activity("room-1", True)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock(
            side_effect=Exception("network down")
        )

        result = await link.report_activity("room-1", True)

        assert result is False

    def test_real_client_exposes_activity_method(self):
        """Guard: the real REST client must actually expose the activity method.

        The AsyncMock-based tests above auto-fabricate attributes, so they would
        stay green even if `agent_api_activity.report_agent_chat_activity`
        disappeared or was renamed in a band_rest bump. This test pins the
        real wire contract: instantiate the real client and assert the method
        exists and is callable.
        """

        client = AsyncRestClient(api_key="test-key", base_url="https://test.com")
        method = getattr(client.agent_api_activity, "report_agent_chat_activity", None)
        assert callable(method)

    @pytest.mark.asyncio
    async def test_repeated_failures_warn_once_then_recover(self, caplog):

        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock(
            side_effect=Exception("down")
        )

        with caplog.at_level(logging.WARNING, logger="band.platform.message_lifecycle"):
            assert await link.report_activity("room-1", True) is False
            assert await link.report_activity("room-1", True) is False

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1  # debounced: only the first failure warns

        # Recovery logs once at INFO and re-arms the warning.
        link.rest.agent_api_activity.report_agent_chat_activity = AsyncMock()
        with caplog.at_level(logging.INFO, logger="band.platform.message_lifecycle"):
            caplog.clear()
            assert await link.report_activity("room-1", True) is True
        assert any("recovered" in r.message.lower() for r in caplog.records)
