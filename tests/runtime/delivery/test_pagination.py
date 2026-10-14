"""Pages of failed rows are scanned at once; the end of the list returns to idle."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import DeliveryStatus
from band.runtime.types import ACTIONABLE_PAGE_SIZE
from tests.runtime.delivery.kit import (
    RestPeer,
    control,
    read,
    started_session,
    turn,
    write,
)

pytestmark = pytest.mark.looptime

# A full page of failed rows and half of the next, then the new input.
FAILED_ROWS = ACTIONABLE_PAGE_SIZE + ACTIONABLE_PAGE_SIZE // 2
LAST_ON_FIRST_PAGE = f"failed-{ACTIONABLE_PAGE_SIZE - 1:03d}"
EARLIER_ROW = "failed-007"
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


def post_failed_rows(peer: RestPeer, count: int) -> None:
    for index in range(count):
        peer.post(f"failed-{index:03d}", status=DeliveryStatus.FAILED)


class TestYieldedPagination:
    async def test_the_new_input_is_taken_in_two_passes_at_one_instant(
        self, peer: RestPeer
    ) -> None:
        post_failed_rows(peer, FAILED_ROWS)
        peer.post("new")

        async with started_session(peer) as session:
            await session.quiesce()

        assert (peer.journal, {page.at for page in peer.reads}) == (
            TRAVERSAL,
            {session.started_at},
        )

    @pytest.mark.parametrize("wake", ["arrival", "resync"])
    async def test_a_later_wake_scans_through_at_one_instant(
        self, peer: RestPeer, wake: str
    ) -> None:
        async with started_session(peer) as session:
            await session.quiesce()
            post_failed_rows(peer, FAILED_ROWS)
            event = peer.post("new")
            seen = len(peer.journal), len(peer.reads)
            woke_at = asyncio.get_running_loop().time()
            if wake == "arrival":
                await session.deliver(event)
            else:
                await session.ctx.request_resync()
            await session.quiesce()

        assert (
            peer.journal[seen[0] :],
            {page.at for page in peer.reads[seen[1] :]},
        ) == (TRAVERSAL, {woke_at})

    async def test_a_control_arriving_mid_traversal_runs_before_the_new_input(
        self, peer: RestPeer
    ) -> None:
        post_failed_rows(peer, FAILED_ROWS)
        peer.post("new")
        approval = peer.post("ctl", at=ACTIONABLE_PAGE_SIZE / 2 + 0.5, listed=False)

        async with started_session(peer, controls=frozenset({"ctl"})) as session:

            async def arrive_after_the_first_page() -> None:
                await peer.page_served(1)
                await session.deliver(approval)

            arrival = asyncio.create_task(arrive_after_the_first_page())
            await session.quiesce()
            await arrival

        assert peer.journal == [
            read(None),
            read(LAST_ON_FIRST_PAGE),
            write(DeliveryStatus.PROCESSING, "ctl"),
            control("ctl"),
            write(DeliveryStatus.PROCESSED, "ctl"),
            read(LAST_ON_FIRST_PAGE),
            write(DeliveryStatus.PROCESSING, "new"),
            turn("new"),
            write(DeliveryStatus.PROCESSED, "new"),
            read(LAST_ON_FIRST_PAGE),
        ]

    async def test_the_end_of_the_list_returns_to_the_idle_cadence(
        self, peer: RestPeer
    ) -> None:
        post_failed_rows(peer, 3)

        async with started_session(peer) as session:
            await asyncio.sleep(IDLE_WAKES[-1] + 1)

        assert (
            session.since_start([page.at for page in peer.reads]),
            [page.cursor for page in peer.reads],
        ) == (pytest.approx(IDLE_WAKES), [None] * len(IDLE_WAKES))

    async def test_a_row_reopened_before_the_traversal_reset_is_found_again(
        self, peer: RestPeer
    ) -> None:
        post_failed_rows(peer, FAILED_ROWS)
        peer.post("new")

        async with started_session(peer) as session:
            await session.settled("new")
            peer.set_status(EARLIER_ROW, DeliveryStatus.PROCESSING)
            await session.resync()

        assert session.turns.ids == [("new",), (EARLIER_ROW,)]
