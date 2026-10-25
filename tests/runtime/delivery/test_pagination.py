"""Pages of failed rows are scanned at once; the end of the list returns to idle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import MessageEvent
from band.platform.message_lifecycle import ACTIONABLE_PAGE_SIZE
from tests.runtime.delivery.kit import (
    EARLIER_ROW,
    FAILED_ROWS,
    IDLE_INTERVAL,
    LAST_ON_FIRST_PAGE,
    RestPeer,
    Session,
    control,
    read,
    started_session,
    turn,
    write,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import ControlledClock, controlled_clock

pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())

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

WakeAction = Callable[[Session, MessageEvent, ControlledClock], Awaitable[float]]


async def _wake_by_arrival(
    session: Session, event: MessageEvent, clock: ControlledClock
) -> float:
    woke_at = asyncio.get_running_loop().time()
    await session.deliver(event)
    await clock.elapse(0)
    await session.quiesce()
    return woke_at


async def _wake_by_resync(
    session: Session, _event: MessageEvent, clock: ControlledClock
) -> float:
    woke_at = asyncio.get_running_loop().time()
    await session.ctx.request_resync()
    await clock.elapse(0)
    await session.quiesce()
    return woke_at


async def _wake_periodically(
    session: Session, _event: MessageEvent, clock: ControlledClock
) -> float:
    woke_at = session.started_at + IDLE_INTERVAL
    await clock.elapse(IDLE_INTERVAL)
    await session.quiesce()
    return woke_at


class TestYieldedPagination:
    async def test_the_new_input_is_taken_in_two_passes_at_one_instant(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")

        async with started_session(rest_peer, batching=True) as session:
            await session.quiesce()

        assert (rest_peer.journal, {page.at for page in rest_peer.reads}) == (
            TRAVERSAL,
            {session.started_at},
        )

    async def _assert_later_wake_scans_through_at_one_instant(
        self, rest_peer: RestPeer, wake: WakeAction
    ) -> None:
        async with (
            started_session(rest_peer, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            rest_peer.post_failed(FAILED_ROWS)
            event = rest_peer.post("new")
            journal_from, reads_from = len(rest_peer.journal), len(rest_peer.reads)
            woke_at = await wake(session, event, clock)

        assert (
            rest_peer.journal[journal_from:],
            {page.at for page in rest_peer.reads[reads_from:]},
        ) == (TRAVERSAL, {woke_at})

    async def test_an_arrival_wake_scans_through_at_one_instant(
        self, rest_peer: RestPeer
    ) -> None:
        await self._assert_later_wake_scans_through_at_one_instant(
            rest_peer, _wake_by_arrival
        )

    async def test_an_explicit_resync_wake_scans_through_at_one_instant(
        self, rest_peer: RestPeer
    ) -> None:
        await self._assert_later_wake_scans_through_at_one_instant(
            rest_peer, _wake_by_resync
        )

    async def test_a_periodic_wake_scans_through_at_one_instant(
        self, rest_peer: RestPeer
    ) -> None:
        await self._assert_later_wake_scans_through_at_one_instant(
            rest_peer, _wake_periodically
        )

    async def test_a_control_arriving_mid_traversal_runs_before_the_new_input(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")
        approval = rest_peer.post(
            "ctl", at=ACTIONABLE_PAGE_SIZE / 2 + 0.5, listed=False
        )

        async with started_session(
            rest_peer, controls=frozenset({"ctl"}), batching=True
        ) as session:

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

        async with (
            started_session(rest_peer, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            for expected_count, delay in enumerate(
                (IDLE_INTERVAL, 2 * IDLE_INTERVAL, 2 * IDLE_INTERVAL), start=1
            ):
                await clock.elapse(delay - 1)
                await session.quiesce()
                assert len(rest_peer.reads) == expected_count
                await clock.elapse(1)
                await session.quiesce()

        assert (
            session.since_start([page.at for page in rest_peer.reads]),
            [page.cursor for page in rest_peer.reads],
        ) == (pytest.approx(IDLE_WAKES), [None] * len(IDLE_WAKES))

    async def test_a_row_reopened_before_the_traversal_reset_is_found_again(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("new")

        async with started_session(rest_peer, batching=True) as session:
            await session.settled("new")
            rest_peer.set_status(EARLIER_ROW, DeliveryStatus.PROCESSING)
            await session.resync()

        assert session.turns.ids == [("new",), (EARLIER_ROW,)]
