"""A skipped /next head still yields later listed work and is not an empty room."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import WAIT_SECONDS

pytestmark = pytest.mark.looptime

STUCK = "stuck"
LATER = "later"
EXHAUST_STUCK = frozenset({STUCK})


def hold_first_listing(peer: RestPeer) -> tuple[asyncio.Event, asyncio.Event]:
    """Pause the first actionable-list response so a control can land across it."""
    held = asyncio.Event()
    release = asyncio.Event()
    answer = peer.answer

    async def barrier(request: httpx.Request) -> httpx.Response:
        response = answer(request)
        listing = request.method == "GET" and request.url.path.endswith("/messages")
        if listing and not held.is_set():
            held.set()
            await release.wait()
        return response

    peer.answer = barrier
    return held, release


class TestExhaustedHead:
    async def test_later_listed_row_runs_while_the_failed_head_stays_failed(
        self,
    ) -> None:
        peer = RestPeer()
        peer.post(STUCK)
        peer.post(LATER)

        async with started_session(peer, fail_on=EXHAUST_STUCK) as session:
            await session.settled(LATER)

        assert (session.turns.ids, peer.status_of(STUCK)) == (
            [(STUCK,), (LATER,)],
            DeliveryStatus.FAILED,
        )

    async def test_a_later_rest_row_is_recovered_after_retries_exhaust_the_head(
        self,
    ) -> None:
        peer = RestPeer()
        peer.post(STUCK)

        async with started_session(peer, fail_on=EXHAUST_STUCK) as session:
            await session.reached(DeliveryStatus.FAILED, STUCK)
            peer.post(LATER)
            await session.resync()
            await session.settled(LATER)

        assert (session.turns.ids, peer.status_of(STUCK)) == (
            [(STUCK,), (LATER,)],
            DeliveryStatus.FAILED,
        )

    async def test_stop_during_listing_does_not_admit_the_later_row(self) -> None:
        peer = RestPeer()
        peer.post(STUCK)
        peer.post(LATER)
        held, release = hold_first_listing(peer)

        async with started_session(peer, fail_on=EXHAUST_STUCK) as session:
            async with asyncio.timeout(WAIT_SECONDS):
                await held.wait()
                session.ctx.stop_room()
                release.set()
            await session.quiesce()

        assert (
            session.turns.ids,
            session.admission_attempts,
            peer.status_of(LATER),
        ) == ([(STUCK,)], [STUCK], None)

    async def test_interrupt_during_listing_still_recovers_the_later_row(self) -> None:
        peer = RestPeer()
        peer.post(STUCK)
        peer.post(LATER)
        held, release = hold_first_listing(peer)

        async with started_session(peer, fail_on=EXHAUST_STUCK) as session:
            async with asyncio.timeout(WAIT_SECONDS):
                await held.wait()
                session.ctx.interrupt()
                release.set()
            await session.settled(LATER)

        assert (session.turns.ids, peer.status_of(STUCK)) == (
            [(STUCK,), (LATER,)],
            DeliveryStatus.FAILED,
        )

    @pytest.mark.looptime(end=1000)
    async def test_a_failed_only_listing_keeps_the_base_resync_interval(self) -> None:
        peer = RestPeer()
        peer.post(STUCK)

        async with started_session(peer, fail_on=EXHAUST_STUCK) as session:
            await session.reached(DeliveryStatus.FAILED, STUCK)
            base = session.ctx.config.idle_resync_seconds
            await asyncio.sleep(3 * base + 1)
            periodic = [t for t in session.since_start(peer.nexts) if t > 0]

        assert periodic == pytest.approx([base, 2 * base, 3 * base])
        assert session.turns.ids == [(STUCK,)]
        assert peer.status_of(STUCK) is DeliveryStatus.FAILED
