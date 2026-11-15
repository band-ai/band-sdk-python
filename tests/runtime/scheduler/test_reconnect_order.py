"""A retained original keeps its position against backlog a reconnect reveals."""

from __future__ import annotations

import pytest

from band.platform.event import ReconnectedEvent
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestReconnectOrder:
    async def test_older_unlisted_original_runs_before_newer_listed_backlog(
        self,
    ) -> None:
        peer = RestPeer()
        async with started_session(peer, gate=True) as session:
            await session.quiesce()
            await session.deliver(peer.post("warmup", at=1))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            # A thread reply REST omits arrives over the socket during the turn...
            await session.deliver(peer.post("thread-reply", at=2, listed=False))
            # ...then a later message the socket missed while disconnected.
            peer.post("missed", at=3)
            await session.deliver(ReconnectedEvent())
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("missed")

        assert session.turns.ids == [("warmup",), ("thread-reply",), ("missed",)]
