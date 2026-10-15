"""A delivery the platform reports failed for this agent never reaches a batch."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.types import delivery_status_for_agent
from tests.platform.settlement.kit import link_over
from tests.runtime.delivery.kit import OTHER_AGENT_ID, RestPeer, started_session
from tests.runtime.helpers import AGENT_ID, ROOM_ID, rest_client_over

pytestmark = pytest.mark.looptime


class TestWireExclusion:
    async def test_the_client_surfaces_each_recipients_status_in_page_metadata(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("failed", status=DeliveryStatus.FAILED)
        rest_peer.post("fresh")
        async with rest_client_over(rest_peer.answer) as rest:
            page = await link_over(rest).get_actionable_page(ROOM_ID)

        assert [
            delivery_status_for_agent(message.metadata, AGENT_ID)
            for message in page.messages
        ] == [DeliveryStatus.FAILED, None]

    async def test_a_failed_row_is_skipped_and_the_rest_run_as_one_turn(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("failed", status=DeliveryStatus.FAILED)
        rest_peer.post(
            "failed-for-another", others={OTHER_AGENT_ID: DeliveryStatus.FAILED}
        )
        rest_peer.post("interrupted", status=DeliveryStatus.PROCESSING)
        rest_peer.post("fresh")

        async with started_session(rest_peer) as session:
            await session.settled("failed-for-another", "interrupted", "fresh")

        assert (
            session.turns.ids,
            session.admission_attempts,
            rest_peer.status_of("failed"),
        ) == (
            [("failed-for-another", "interrupted", "fresh")],
            ["failed-for-another", "interrupted", "fresh"],
            DeliveryStatus.FAILED,
        )

    async def test_switching_batching_on_does_not_replay_inputs_failed_before(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("failed-earlier")
        async with started_session(
            rest_peer, batching=False, fail_on=frozenset({"failed-earlier"})
        ) as before:
            await before.reached(DeliveryStatus.FAILED, "failed-earlier")
        rest_peer.post("fresh")

        async with started_session(rest_peer, batching=True) as after:
            await after.settled("fresh")

        assert (before.turns.ids, after.turns.ids) == (
            [("failed-earlier",)],
            [("fresh",)],
        )
