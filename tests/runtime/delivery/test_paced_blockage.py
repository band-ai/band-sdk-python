"""True claim, admission, capacity or write blockage retries on the periodic cadence."""

from __future__ import annotations

import asyncio

import pytest
from band_sdk_core import ClaimRegistry

from band.client.streaming import DeliveryStatus
from band.runtime.types import SessionConfig
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime

INTERVAL = SessionConfig().idle_resync_seconds
# The startup pass, then one retry per interval; never a rescan in between.
PACED = [0, INTERVAL, 2 * INTERVAL]
THREE_INTERVALS = 2 * INTERVAL + 1


class TestPacedBlockage:
    async def test_a_refused_admission_is_retried_once_per_interval(
        self, peer: RestPeer
    ) -> None:
        peer.post("m1")
        peer.refuse_processing.add("m1")

        async with started_session(peer) as session:
            await asyncio.sleep(THREE_INTERVALS)

        assert (
            session.since_start(peer.write_times(DeliveryStatus.PROCESSING)),
            session.turns.ids,
        ) == (pytest.approx(PACED), [])

    async def test_a_claim_held_by_another_owner_is_rechecked_once_per_interval(
        self, peer: RestPeer
    ) -> None:
        peer.post("m1")
        claims = ClaimRegistry()
        claims.try_claim(ROOM_ID, "m1")

        async with started_session(peer, claims=claims) as session:
            await asyncio.sleep(THREE_INTERVALS)

        assert (
            session.since_start([page.at for page in peer.reads]),
            session.admission_attempts,
        ) == (pytest.approx(PACED), [])

    async def test_a_busy_host_is_offered_the_input_once_per_interval(
        self, peer: RestPeer
    ) -> None:
        peer.post("m1")

        async with started_session(peer, accepting=False) as session:
            await asyncio.sleep(THREE_INTERVALS)

        assert (
            session.since_start(peer.write_times(DeliveryStatus.PROCESSING)),
            session.turns.ids,
        ) == (pytest.approx(PACED), [])

    async def test_an_unconfirmed_terminal_write_is_retried_without_another_turn(
        self, peer: RestPeer
    ) -> None:
        peer.post("m1")
        peer.confirm_terminal = False

        async with started_session(peer) as session:
            await asyncio.sleep(THREE_INTERVALS)

        assert (
            session.since_start(peer.write_times(DeliveryStatus.PROCESSED)),
            session.turns.ids,
        ) == (pytest.approx(PACED), [("m1",)])
