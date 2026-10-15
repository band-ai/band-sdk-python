"""Priority controls keep fresh delivery eligibility and their original payloads."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ReconnectedEvent
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestControlEligibility:
    @pytest.mark.parametrize("batching", [False])
    @pytest.mark.parametrize(
        "retained_duplicate", [False, True], ids=["rest", "retained"]
    )
    async def test_failed_controls_obey_the_configured_replay_policy(
        self, rest_peer: RestPeer, batching: bool, retained_duplicate: bool
    ) -> None:
        rest_peer.post("head")
        async with started_session(
            rest_peer,
            batching=batching,
            accepting=False,
            controls=frozenset({"approve"}),
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            delayed_original = rest_peer.post("approve")
            rest_peer.set_status("approve", DeliveryStatus.FAILED)
            if retained_duplicate:
                await session.deliver(delayed_original)
            else:
                await session.resync()
            await session.quiesce()

        assert (
            session.controls.handled,
            bool(rest_peer.writes_of(DeliveryStatus.PROCESSING, "approve")),
            rest_peer.status_of("approve"),
        ) == (
            ([], False, DeliveryStatus.FAILED)
            if batching
            else (["approve"], True, DeliveryStatus.PROCESSED)
        )

    async def test_an_eligible_duplicate_keeps_its_delivered_payload(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("head")
        async with started_session(
            rest_peer, accepting=False, controls=frozenset({"approve"})
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            original = rest_peer.post("approve")
            assert original.payload is not None
            original.payload.content = "the delivered approval"
            await session.deliver(original)
            await session.settled("approve")

        assert [payload.content for payload in session.controls.payloads] == [
            "the delivered approval"
        ]

    @pytest.mark.parametrize("opens_question", [True, False], ids=["opens", "closes"])
    async def test_each_candidate_uses_the_question_state_after_the_previous_control(
        self, rest_peer: RestPeer, opens_question: bool
    ) -> None:
        rest_peer.post("head")
        recognized = {"approve"} if opens_question else {"approve", "answer"}
        async with started_session(
            rest_peer, accepting=False, controls=frozenset(recognized)
        ) as session:
            await session.until(lambda: bool(session.turns.refused))
            controls = session.controls
            if opens_question:
                controls.on_handled = lambda message_id: controls.recognized.add(
                    "answer"
                )
            else:
                controls.on_handled = lambda message_id: controls.recognized.discard(
                    "answer"
                )
            approval = rest_peer.post("approve")
            answer = rest_peer.post("answer")
            await session.deliver(approval, answer)
            await session.quiesce()

        assert (controls.handled, rest_peer.status_of("answer")) == (
            (["approve", "answer"], DeliveryStatus.PROCESSED)
            if opens_question
            else (["approve"], None)
        )

    async def test_a_pending_failure_keeps_its_ws_only_original_for_singleton_retry(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.confirm_terminal = False
        original = rest_peer.post("first", listed=False)
        async with started_session(
            rest_peer,
            batching=False,
            retries=2,
            controls=frozenset({"approve"}),
            fail_on=frozenset({"first"}),
        ) as session:
            await session.deliver(original)
            await session.until(
                lambda: bool(rest_peer.writes_of(DeliveryStatus.FAILED, "first"))
            )
            rest_peer.confirm_terminal = True
            await session.deliver(rest_peer.post("ordinary"), rest_peer.post("approve"))
            await session.settled("approve")
            before_confirmation = session.progress

            await session.deliver(ReconnectedEvent())
            await session.settled("ordinary")

        assert (
            before_confirmation,
            session.turns.ids,
            session.controls.handled,
            rest_peer.status_of("first"),
        ) == (
            ([("first",)], ["first", "approve"]),
            [("first",), ("first",), ("ordinary",)],
            ["approve"],
            DeliveryStatus.FAILED,
        )
