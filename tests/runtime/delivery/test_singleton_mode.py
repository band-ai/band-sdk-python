"""With aggregation off every delivery is its own turn under the existing policy."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session, turn, write

pytestmark = pytest.mark.looptime


class TestSingletonMode:
    async def test_three_inputs_queued_behind_a_busy_turn_are_three_turns(
        self, rest_peer: RestPeer
    ) -> None:
        events = [rest_peer.post(message_id) for message_id in ("m1", "m2", "m3")]

        async with started_session(rest_peer, batching=False, gate=True) as session:
            await session.deliver(events[0])
            await session.until(lambda: session.turns.ids == [("m1",)])
            await session.deliver(*events[1:])
            session.turns.release.set()
            await session.settled("m1", "m2", "m3")

        assert session.turns.ids == [("m1",), ("m2",), ("m3",)]

    @pytest.mark.parametrize("retries", [1, 2, 3])
    async def test_a_failing_singleton_is_attempted_as_often_as_the_policy_allows(
        self, rest_peer: RestPeer, retries: int
    ) -> None:
        rest_peer.post("m1")

        async with started_session(
            rest_peer, batching=False, retries=retries, fail_on=frozenset({"m1"})
        ) as session:
            await session.quiesce()

        assert (
            session.turns.ids,
            rest_peer.status_of("m1"),
            rest_peer.events_posted,
        ) == (
            [("m1",)] * retries,
            DeliveryStatus.FAILED,
            1,
        )

    @pytest.mark.parametrize("batching", [False])
    async def test_an_input_runs_only_after_its_admission_is_accepted(
        self, rest_peer: RestPeer, batching: bool
    ) -> None:
        arrival = rest_peer.post("m1")
        rest_peer.refuse_processing.add("m1")

        async with started_session(rest_peer, batching=batching) as session:
            await session.deliver(arrival)
            await session.quiesce()
            ran_while_refused = list(session.turns.ids)
            rest_peer.refuse_processing.clear()
            await session.resync()

        assert (ran_while_refused, rest_peer.without_reads()[-3:]) == (
            [],
            [
                write(DeliveryStatus.PROCESSING, "m1"),
                turn("m1"),
                write(DeliveryStatus.PROCESSED, "m1"),
            ],
        )
