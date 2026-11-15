"""True claim, admission, busy-host or write blockage retries on the periodic cadence."""

from __future__ import annotations

import pytest
from band_sdk_core import ClaimRegistry

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import IDLE_INTERVAL, RestPeer, started_session
from tests.runtime.helpers import ROOM_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

pytestmark = pytest.mark.looptime(resolution=binary_clock_resolution())

# The startup pass, then one retry per interval; never a rescan in between.
PACED = [0, IDLE_INTERVAL, 2 * IDLE_INTERVAL]


class TestPacedBlockage:
    async def test_a_refused_admission_is_retried_once_per_interval(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("m1")
        rest_peer.refuse_processing.add("m1")

        async with (
            started_session(rest_peer, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL - 1)
            await session.quiesce()
            assert session.since_start(
                rest_peer.write_times(DeliveryStatus.PROCESSING)
            ) == pytest.approx([0])
            await clock.elapse(1)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL)
            await session.quiesce()

        assert (
            session.since_start(rest_peer.write_times(DeliveryStatus.PROCESSING)),
            session.turns.ids,
        ) == (pytest.approx(PACED), [])

    async def test_a_claim_held_by_another_owner_is_rechecked_once_per_interval(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("m1")
        claims = ClaimRegistry()
        claims.try_claim(ROOM_ID, "m1")

        async with (
            started_session(rest_peer, claims=claims, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL - 1)
            await session.quiesce()
            assert session.since_start([page.at for page in rest_peer.reads]) == (
                pytest.approx([0])
            )
            await clock.elapse(1)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL)
            await session.quiesce()

        assert (
            session.since_start([page.at for page in rest_peer.reads]),
            session.admission_attempts,
        ) == (pytest.approx(PACED), [])

    async def test_a_busy_host_is_offered_the_input_once_per_interval(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("m1")

        async with (
            started_session(rest_peer, accepting=False, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL - 1)
            await session.quiesce()
            assert session.since_start(
                rest_peer.write_times(DeliveryStatus.PROCESSING)
            ) == pytest.approx([0])
            await clock.elapse(1)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL)
            await session.quiesce()

        assert (
            session.since_start(rest_peer.write_times(DeliveryStatus.PROCESSING)),
            session.turns.ids,
        ) == (pytest.approx(PACED), [])

    async def test_a_refused_input_runs_at_the_next_interval_once_the_host_accepts(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("m1")

        async with (
            started_session(rest_peer, accepting=False, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            assert session.turns.refused == [("m1",)]
            session.turns.accepting = True
            await clock.elapse(IDLE_INTERVAL - 1)
            await session.quiesce()
            assert session.turns.ids == []
            await clock.elapse(1)
            await session.quiesce()

        assert (
            session.turns.ids,
            session.since_start(rest_peer.write_times(DeliveryStatus.PROCESSED)),
        ) == ([("m1",)], pytest.approx([IDLE_INTERVAL]))

    async def test_an_unconfirmed_terminal_write_is_retried_without_another_turn(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("m1")
        rest_peer.confirm_terminal = False

        async with (
            started_session(rest_peer, batching=True) as session,
            controlled_clock() as clock,
        ):
            await clock.elapse(0)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL - 1)
            await session.quiesce()
            assert session.since_start(
                rest_peer.write_times(DeliveryStatus.PROCESSED)
            ) == pytest.approx([0])
            await clock.elapse(1)
            await session.quiesce()
            await clock.elapse(IDLE_INTERVAL)
            await session.quiesce()

        assert (
            session.since_start(rest_peer.write_times(DeliveryStatus.PROCESSED)),
            session.turns.ids,
        ) == (pytest.approx(PACED), [("m1",)])
