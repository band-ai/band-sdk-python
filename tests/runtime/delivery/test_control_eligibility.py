"""Priority controls keep fresh delivery eligibility and their original payloads."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ReconnectedEvent
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime

HEAD = "head"
APPROVAL = "approve"
ANSWER = "answer"
DELIVERED_APPROVAL = "the delivered approval"


class TestControlEligibility:
    @pytest.mark.parametrize("batching", [False])
    async def test_a_failed_rest_control_obeys_the_configured_replay_policy(
        self, rest_peer: RestPeer, batching: bool
    ) -> None:
        rest_peer.post(HEAD)
        async with started_session(
            rest_peer,
            batching=batching,
            accepting=False,
            controls=frozenset({APPROVAL}),
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            rest_peer.post(APPROVAL)
            rest_peer.set_status(APPROVAL, DeliveryStatus.FAILED)
            await session.resync()
            await session.quiesce()

        assert session.controls.handled == [APPROVAL]
        assert rest_peer.writes_of(DeliveryStatus.PROCESSING, APPROVAL)
        assert rest_peer.status_of(APPROVAL) is DeliveryStatus.PROCESSED

    @pytest.mark.parametrize("batching", [False])
    async def test_a_failed_retained_control_obeys_the_configured_replay_policy(
        self, rest_peer: RestPeer, batching: bool
    ) -> None:
        rest_peer.post(HEAD)
        async with started_session(
            rest_peer,
            batching=batching,
            accepting=False,
            controls=frozenset({APPROVAL}),
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            delayed_original = rest_peer.post(APPROVAL)
            rest_peer.set_status(APPROVAL, DeliveryStatus.FAILED)
            await session.deliver(delayed_original)
            await session.quiesce()

        assert session.controls.handled == [APPROVAL]
        assert rest_peer.writes_of(DeliveryStatus.PROCESSING, APPROVAL)
        assert rest_peer.status_of(APPROVAL) is DeliveryStatus.PROCESSED

    async def test_an_eligible_duplicate_keeps_its_delivered_payload(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(HEAD)
        async with started_session(
            rest_peer, accepting=False, controls=frozenset({APPROVAL})
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            original = rest_peer.post(APPROVAL)
            assert original.payload is not None
            original.payload.content = DELIVERED_APPROVAL
            await session.deliver(original)
            await session.settled(APPROVAL)

        assert [payload.content for payload in session.controls.payloads] == [
            DELIVERED_APPROVAL
        ]

    async def test_a_control_can_open_the_question_for_the_next_candidate(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(HEAD)
        async with started_session(
            rest_peer, accepting=False, controls=frozenset({APPROVAL})
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            controls = session.controls
            controls.on_handled = lambda message_id: controls.recognized.add(ANSWER)
            approval = rest_peer.post(APPROVAL)
            answer = rest_peer.post(ANSWER)
            await session.deliver(approval, answer)
            await session.quiesce()

        assert controls.handled == [APPROVAL, ANSWER]
        assert rest_peer.status_of(ANSWER) is DeliveryStatus.PROCESSED

    async def test_a_control_can_close_the_question_for_the_next_candidate(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post(HEAD)
        async with started_session(
            rest_peer, accepting=False, controls=frozenset({APPROVAL, ANSWER})
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            controls = session.controls
            controls.on_handled = lambda message_id: controls.recognized.discard(ANSWER)
            approval = rest_peer.post(APPROVAL)
            answer = rest_peer.post(ANSWER)
            await session.deliver(approval, answer)
            await session.quiesce()

        assert controls.handled == [APPROVAL]
        assert rest_peer.status_of(ANSWER) is None

    async def test_a_pending_failure_keeps_its_ws_only_original_for_singleton_retry(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.confirm_terminal = False
        original = rest_peer.post("first", listed=False)
        async with started_session(
            rest_peer,
            batching=False,
            retries=2,
            controls=frozenset({APPROVAL}),
            fail_on=frozenset({"first"}),
        ) as session:
            await session.deliver(original)
            await session.until(
                lambda: bool(rest_peer.writes_of(DeliveryStatus.FAILED, "first"))
            )
            rest_peer.confirm_terminal = True
            await session.deliver(rest_peer.post("ordinary"), rest_peer.post(APPROVAL))
            await session.settled(APPROVAL)
            before_confirmation = session.progress

            await session.deliver(ReconnectedEvent())
            await session.settled("ordinary")

        assert (
            before_confirmation,
            session.turns.ids,
            session.controls.handled,
            rest_peer.status_of("first"),
        ) == (
            ([("first",)], ["first", APPROVAL]),
            [("first",), ("first",), ("ordinary",)],
            [APPROVAL],
            DeliveryStatus.FAILED,
        )
