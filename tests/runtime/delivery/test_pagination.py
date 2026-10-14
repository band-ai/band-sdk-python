"""Pages of failed rows are scanned at once; the end of the list returns to idle."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import DeliveryStatus
from band.runtime.types import ACTIONABLE_PAGE_SIZE
from tests.runtime.delivery.kit import (
    EARLIER_ROW,
    FAILED_ROWS,
    IDLE_INTERVAL,
    LAST_ON_FIRST_PAGE,
    RestPeer,
    control,
    read,
    started_session,
    turn,
    write,
)

pytestmark = pytest.mark.looptime

# The interval doubles on each empty wake up to its cap.
IDLE_WAKES = [0, 60, 180, 300]
# Two passes find the input; the third finds the rest of the second page skipped.
TRAVERSAL = [
    read(None),
    read(LAST_ON_FIRST_PAGE),
    write(DeliveryStatus.PROCESSING, "new"),
    turn("new"),
    write(DeliveryStatus.PROCESSED, "new"),
    read(LAST_ON_FIRST_PAGE),
]


class TestYieldedPagination:
    async def test_the_new_input_is_taken_in_two_passes_at_one_instant(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")

        async with started_session(rest_peer) as session:
            await session.quiesce()

        assert (rest_peer.journal, {page.at for page in rest_peer.reads}) == (
            TRAVERSAL,
            {session.started_at},
        )

    @pytest.mark.parametrize("wake", ["arrival", "resync", "periodic"])
    async def test_a_later_wake_scans_through_at_one_instant(
        self, rest_peer: RestPeer, wake: str
    ) -> None:
        async with started_session(rest_peer) as session:
            await session.quiesce()
            rest_peer.post_failed(FAILED_ROWS)
            event = rest_peer.post("new")
            journal_from, reads_from = len(rest_peer.journal), len(rest_peer.reads)
            woke_at = asyncio.get_running_loop().time()
            match wake:
                case "arrival":
                    await session.deliver(event)
                    await session.quiesce()
                case "resync":
                    await session.ctx.request_resync()
                    await session.quiesce()
                case "periodic":
                    woke_at = session.started_at + IDLE_INTERVAL
                    await asyncio.sleep(IDLE_INTERVAL)

        assert (
            rest_peer.journal[journal_from:],
            {page.at for page in rest_peer.reads[reads_from:]},
        ) == (TRAVERSAL, {woke_at})

    async def test_a_control_arriving_mid_traversal_runs_before_the_new_input(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")
        approval = rest_peer.post(
            "ctl", at=ACTIONABLE_PAGE_SIZE / 2 + 0.5, listed=False
        )

        async with started_session(rest_peer, controls=frozenset({"ctl"})) as session:

            async def arrive_after_the_first_page() -> None:
                await rest_peer.page_served(1)
                await session.deliver(approval)

            arrival = asyncio.create_task(arrive_after_the_first_page())
            await session.quiesce()
            await arrival

        assert rest_peer.without_reads() == [
            write(DeliveryStatus.PROCESSING, "ctl"),
            control("ctl"),
            write(DeliveryStatus.PROCESSED, "ctl"),
            write(DeliveryStatus.PROCESSING, "new"),
            turn("new"),
            write(DeliveryStatus.PROCESSED, "new"),
        ]

    async def test_the_end_of_the_list_returns_to_the_idle_cadence(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(3)

        async with started_session(rest_peer) as session:
            await asyncio.sleep(IDLE_WAKES[-1] + 1)

        assert (
            session.since_start([page.at for page in rest_peer.reads]),
            [page.cursor for page in rest_peer.reads],
        ) == (pytest.approx(IDLE_WAKES), [None] * len(IDLE_WAKES))

    async def test_a_row_reopened_before_the_traversal_reset_is_found_again(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")

        async with started_session(rest_peer) as session:
            await session.settled("new")
            rest_peer.set_status(EARLIER_ROW, DeliveryStatus.PROCESSING)
            await session.resync()

        assert session.turns.ids == [("new",), (EARLIER_ROW,)]
