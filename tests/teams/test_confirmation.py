"""Confirm exactly one immutable reply through independently acknowledged dispatch."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bandteams.evidence import Delivery, Receipt
from bandteams.proposals import Dispatcher, Refused


class TestConfirmation:
    async def test_acknowledgement_and_duplicate_return_while_remote_check_is_blocked(
        self, proposals: Any, person: Any, origin: Any
    ) -> None:
        claim = proposals.stage(
            person, "room-one", "recipient-one", origin, "Exact reviewed body"
        )
        proposals.arm(claim.proposal_id, "review-card")
        action = origin.model_copy(update={"activity": "review-card"})
        checking, release = (asyncio.Event(), asyncio.Event())
        sent = []

        async def check(claim: Any) -> Any:
            checking.set()
            await release.wait()

        async def send(claim: Any, body: Any) -> Any:
            sent.append((claim.room, claim.recipient, body))
            return "00000000-0000-0000-0000-000000000001"

        async def receipt(claim: Any) -> Any:
            raise OSError("receipt transport unavailable")

        dispatcher = Dispatcher(proposals, check, send, receipt)
        try:
            first = await dispatcher.confirm(claim.proposal_id, 1, action)
            await checking.wait()
            duplicate = await dispatcher.confirm(claim.proposal_id, 1, action)
            assert (first.delivery, duplicate.delivery, sent) == (
                Delivery.PENDING,
                Delivery.PENDING,
                [],
            )
            worker = dispatcher.tasks[claim.proposal_id]
            release.set()
            await worker
            assert sent == [("room-one", "recipient-one", "Exact reviewed body")]
            outcome = proposals.claims[claim.proposal_id]
            assert (outcome.delivery, outcome.receipt, outcome.band_id) == (
                Delivery.SENT,
                Receipt.FAILED,
                "00000000-0000-0000-0000-000000000001",
            )
            duplicate = await dispatcher.confirm(claim.proposal_id, 1, action)
            assert duplicate == outcome
        finally:
            await dispatcher.stop(person.alias)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("tenant", "foreign"),
            ("actor", "foreign"),
            ("team", "foreign"),
            ("channel", "foreign"),
            ("conversation", "foreign"),
            ("activity", "foreign"),
        ],
    )
    def test_foreign_action_cannot_consume_review(
        self, proposals: Any, person: Any, origin: Any, field: Any, value: Any
    ) -> None:
        claim = proposals.stage(person, "room-one", "recipient-one", origin, "Body")
        proposals.arm(claim.proposal_id, "review-card")
        action = origin.model_copy(update={"activity": "review-card", field: value})
        with pytest.raises(Refused):
            proposals.consume(claim.proposal_id, 1, action)
        assert proposals.claims[claim.proposal_id].delivery == Delivery.REVIEW

    def test_new_link_fences_old_review(
        self, proposals: Any, person: Any, origin: Any
    ) -> None:
        claim = proposals.stage(person, "room-one", "recipient-one", origin, "Body")
        proposals.arm(claim.proposal_id, "review-card")
        proposals.activate(person)
        with pytest.raises(Refused):
            proposals.consume(
                claim.proposal_id,
                1,
                origin.model_copy(update={"activity": "review-card"}),
            )
        assert proposals.claims[claim.proposal_id].delivery == Delivery.CANCELLED
