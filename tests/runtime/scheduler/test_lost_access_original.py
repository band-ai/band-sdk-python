"""An original whose claim 404s while the room is unreadable waits for access to return."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ReconnectedEvent
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestLostAccessOriginal:
    async def test_thread_reply_claimed_while_removed_runs_after_readd(self) -> None:
        peer = RestPeer()
        async with started_session(peer, gate=True) as session:
            await session.quiesce()
            await session.deliver(peer.post("warmup", at=1))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            # A thread reply: carried by the WebSocket, omitted by /next and the list.
            await session.deliver(peer.post("reply", at=2, listed=False))
            # Removed while the socket is down, so no room_removed event arrives.
            peer.participant = False
            session.turns.gate = False
            session.turns.release.set()
            await session.quiesce()

            # Re-added before the socket reconnects; the reconnect resyncs the room.
            peer.participant = True
            await session.deliver(ReconnectedEvent())
            await session.quiesce()

        assert session.turns.ids == [("warmup",), ("reply",)]
        assert peer.status_of("reply") is DeliveryStatus.PROCESSED
