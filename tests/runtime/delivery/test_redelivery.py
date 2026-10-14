"""An original delivered again by either source is not reopened or run twice."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime

RAN_ONCE = ([("m1",)], ["m1"])


@pytest.mark.parametrize("batching", [True, False], ids=["batching", "singleton"])
class TestRedelivery:
    async def test_the_same_delivery_from_both_sources_runs_once(
        self, peer: RestPeer, batching: bool
    ) -> None:
        event = peer.post("m1")

        async with started_session(peer, batching=batching) as session:
            await session.deliver(event, event)
            await session.settled("m1")
            await session.deliver(event)
            await session.resync()

        assert session.progress == RAN_ONCE

    async def test_a_duplicate_arriving_while_the_original_runs_does_not_rerun_it(
        self, peer: RestPeer, batching: bool
    ) -> None:
        event = peer.post("m1")

        async with started_session(peer, batching=batching, gate=True) as session:
            await session.until(lambda: session.turns.ids == [("m1",)])
            await session.deliver(event)
            await session.ctx.request_resync()
            session.turns.release.set()
            await session.settled("m1")
            await session.resync()

        assert session.progress == RAN_ONCE

    async def test_an_original_awaiting_its_terminal_write_is_not_reopened(
        self, peer: RestPeer, batching: bool
    ) -> None:
        event = peer.post("m1")
        peer.confirm_terminal = False

        async with started_session(peer, batching=batching) as session:
            await session.quiesce()
            await session.deliver(event)
            await session.resync()
            awaiting = session.progress
            peer.confirm_terminal = True
            await session.resync()

        assert (awaiting, session.progress, peer.status_of("m1")) == (
            RAN_ONCE,
            RAN_ONCE,
            DeliveryStatus.PROCESSED,
        )

    async def test_a_completed_original_listed_again_is_not_reopened(
        self, peer: RestPeer, batching: bool
    ) -> None:
        event = peer.post("m1")

        async with started_session(peer, batching=batching) as session:
            await session.settled("m1")
            # A lagging read, or a reopen from elsewhere, lists it as unstarted.
            peer.set_status("m1", None)
            await session.deliver(event)
            await session.resync()

        assert session.progress == RAN_ONCE
