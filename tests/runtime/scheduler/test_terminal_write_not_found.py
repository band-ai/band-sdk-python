"""A terminal write answered 404 is dropped when the message is gone and kept when the room is."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ReconnectedEvent
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestTerminalWriteNotFound:
    async def test_failed_write_for_a_deleted_message_is_sent_once(self) -> None:
        peer = RestPeer()
        async with started_session(
            peer, gate=True, retries=3, fail_on=frozenset({"doomed"})
        ) as session:
            await session.quiesce()
            await session.deliver(peer.post("doomed", at=1))
            await session.until(lambda: session.turns.ids == [("doomed",)])
            peer.delete("doomed")
            session.turns.gate = False
            session.turns.release.set()
            await session.quiesce()
            await session.deliver(peer.post("live", at=2))
            await session.quiesce()
            for _ in range(3):
                await session.deliver(ReconnectedEvent())
                await session.quiesce()
            await session.deliver(peer.post("after", at=3))
            await session.quiesce()

        assert session.turns.ids == [("doomed",), ("live",), ("after",)]
        assert len(peer.writes_of(DeliveryStatus.FAILED, "doomed")) == 1

    async def test_failed_write_refused_while_removed_retries_after_readd(
        self,
    ) -> None:
        peer = RestPeer()
        async with started_session(
            peer, gate=True, retries=2, fail_on=frozenset({"flaky"})
        ) as session:
            await session.quiesce()
            await session.deliver(peer.post("flaky", at=1))
            await session.until(lambda: session.turns.ids == [("flaky",)])
            peer.participant = False
            session.turns.gate = False
            session.turns.release.set()
            await session.quiesce()

            peer.participant = True
            await session.deliver(ReconnectedEvent())
            await session.quiesce()

        assert session.turns.ids == [("flaky",), ("flaky",)]
        assert peer.status_of("flaky") is DeliveryStatus.FAILED
