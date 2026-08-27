"""BandLink behavior proven against a real (fake-server-backed) wire, not
mocks -- covers what mocked tests in test_link.py structurally can't: real
Phoenix protocol round trips through WebSocketClient/PHXChannelsClient.

See tests/testing/test_phoenix_server.py for tests of the fake server's own
protocol mechanics (default join outcome, leave acks, push delivery, close
vs. abort semantics); this file is scoped to BandLink-specific behavior the
fake exists to prove.
"""

from __future__ import annotations

from band.platform.link import BandLink
from band.testing import JoinOutcome, fake_phoenix_server


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
