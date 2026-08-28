"""BandLink behavior proven against a real (fake-server-backed) wire, not
mocks -- covers what mocked tests in test_link.py structurally can't: real
Phoenix protocol round trips through WebSocketClient/PHXChannelsClient.

See tests/testing/test_phoenix_server.py for tests of the fake server's own
protocol mechanics (default join outcome, leave acks, push delivery, close
vs. abort semantics); this file is scoped to BandLink-specific behavior the
fake exists to prove.
"""

from __future__ import annotations

import asyncio

from band.platform.link import BandLink
from band.testing import JoinOutcome, fake_phoenix_server

from tests.conftest import spy_on_reconciliation_drain


def make_link(server_url: str) -> BandLink:
    return BandLink(
        agent_id="agent-123",
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
    )


async def test_room_participants_rejection_rolls_back_chat_room_over_the_real_wire() -> (
    None
):
    """The two-phase room join's rollback (subscribe_room's second-join
    failure path) sends a real phx_leave for chat_room and gets it acked by
    a real server -- a mocked leave_chat_room_channel call can only prove
    BandLink *attempted* the rollback, never that the wire round trip is
    actually correct."""
    async with fake_phoenix_server(
        join_outcomes={"room_participants:room-1": [JoinOutcome.REJECTED]}
    ) as server:
        link = make_link(server.url)
        await link.connect()

        await link.subscribe_room("room-1")

        assert link.is_room_subscribed("room-1") is False
        # The rollback's leave actually reached the server and was acked --
        # not just that BandLink called a mocked leave method.
        assert "chat_room:room-1" not in server.joined_topics
        assert "room_participants:room-1" not in server.joined_topics


async def test_room_participants_rejoin_failure_marks_room_unsubscribed() -> None:
    """A topic that joined fine once but genuinely fails to REJOIN after a
    reconnect (not an initial-join failure -- subscribe_room's own rollback
    path already covers that) must flip is_room_subscribed() to False, not
    leave it silently stale forever."""
    async with fake_phoenix_server(
        join_outcomes={
            "room_participants:room-1": [JoinOutcome.OK, JoinOutcome.REJECTED],
        }
    ) as server:
        link = make_link(server.url)
        await link.connect()
        await link.subscribe_room("room-1")
        assert link.is_room_subscribed("room-1") is True

        reconnect_handled = spy_on_reconciliation_drain(link)
        await server.abort_connection()
        await asyncio.wait_for(reconnect_handled.wait(), timeout=5.0)

        assert link.is_room_subscribed("room-1") is False
        assert "room_participants:room-1" not in server.joined_topics
        assert (
            "chat_room:room-1" not in server.joined_topics
        )  # forced clean of both topics


async def test_agent_rooms_rejoin_failure_marks_topic_unjoined() -> None:
    """Same rejoin-failure detection as the room test, for the single-topic
    agent channels. No public BandLink-level read exists for agent-topic
    membership (only ``is_room_subscribed`` does), so this reaches into
    ``link._subscriptions`` -- justified since adding a public accessor with
    no real caller besides this test would be speculative surface."""
    async with fake_phoenix_server(
        join_outcomes={
            "agent_rooms:agent-123": [JoinOutcome.OK, JoinOutcome.REJECTED],
        }
    ) as server:
        link = make_link(server.url)
        await link.connect()
        await link.subscribe_agent_rooms("agent-123")
        assert (
            link._subscriptions.is_agent_topic_joined("agent_rooms:agent-123") is True
        )

        reconnect_handled = spy_on_reconciliation_drain(link)
        await server.abort_connection()
        await asyncio.wait_for(reconnect_handled.wait(), timeout=5.0)

        assert (
            link._subscriptions.is_agent_topic_joined("agent_rooms:agent-123") is False
        )
        assert "agent_rooms:agent-123" not in server.joined_topics
