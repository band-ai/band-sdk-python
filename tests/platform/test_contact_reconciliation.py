"""ContactTopicRaceAndReconciliation behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from band_sdk_core import AgentTopicKind, AgentTopicStatus

from band.platform.link import BandLink
from tests.platform.conftest import cancelled_mid_await


class TestContactTopicRaceAndReconciliation:
    """SubscriptionTracker-backed dedup, reconciliation blocking, and
    cancellation safety for the agent-level topics — mirrors
    TestBandLinkSubscriptionRaceAndReconciliation in test_link.py for the
    room path, scoped to agent_contacts (agent_rooms shares the same
    _subscribe_agent_topic/_leave_agent_topic helpers)."""

    @patch("band.platform.link.WebSocketClient")
    async def test_concurrent_subscribe_agent_contacts_only_one_join(
        self, mock_ws_class, mock_ws_client
    ):
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await asyncio.gather(
            link.subscribe_agent_contacts("agent-123"),
            link.subscribe_agent_contacts("agent-123"),
        )

        assert mock_ws_client.join_agent_contacts_channel.call_count == 1

    @patch("band.platform.link.WebSocketClient")
    async def test_ordinary_join_failure_is_not_blocking(
        self, mock_ws_class, mock_ws_client
    ):
        """An ordinary (non-cancelled) join failure resolves cleanly via
        record_agent_topic_join(joined=False) — settled=True before the
        finally block, so unlike cancellation it never reaches the local
        reconciliation set. A retry must succeed immediately, no reconnect
        needed."""
        mock_ws_class.return_value = mock_ws_client
        mock_ws_client.join_agent_contacts_channel.side_effect = Exception(
            "join failed"
        )

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await link.subscribe_agent_contacts("agent-123")

        mock_ws_client.join_agent_contacts_channel.side_effect = None
        mock_ws_client.join_agent_contacts_channel.reset_mock()
        await link.subscribe_agent_contacts("agent-123")

        # A genuinely fresh join attempt, not the retry silently no-opping
        # as if still blocked.
        mock_ws_client.join_agent_contacts_channel.assert_called_once()

    @patch("band.platform.link.WebSocketClient")
    async def test_cancelled_join_blocks_agent_contacts_until_reconnect(
        self, mock_ws_class, mock_ws_client
    ):
        """A cancel mid-flight (after PHX's own join call has started, proven
        with a gated coroutine) leaves the real transport outcome unknown, so
        record_agent_topic_join_ambiguous resolves core straight to
        NeedsReconciliation instead of Absent — verified directly against the
        tracker, not just the retry-blocking behavior it drives."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        async with cancelled_mid_await(
            mock_ws_client.join_agent_contacts_channel,
            link.subscribe_agent_contacts("agent-123"),
        ):
            pass

        topic = AgentTopicKind.Contacts.topic("agent-123")
        assert (
            link._subscriptions_manager._subscriptions.agent_topic_status(topic)
            == AgentTopicStatus.NeedsReconciliation
        )

        # Blocked: a retry before the next reconnect must not attempt a join.
        mock_ws_client.join_agent_contacts_channel.reset_mock()
        await link.subscribe_agent_contacts("agent-123")
        mock_ws_client.join_agent_contacts_channel.assert_not_called()

        # The reconnect boundary force-leaves before acknowledging, unblocking it.
        mock_ws_client.joined_topics.return_value = frozenset(
            {"agent_control:agent-123"}
        )
        await link._on_reconnected()
        mock_ws_client.leave_agent_contacts_channel.assert_called_once_with("agent-123")

        await link.subscribe_agent_contacts("agent-123")
        mock_ws_client.join_agent_contacts_channel.assert_called_once()
