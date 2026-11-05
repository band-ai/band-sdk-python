"""Expire unconfirmed reviews against a controlled clock."""

from __future__ import annotations

from typing import Any

from bandteams.evidence import Delivery
from bandteams.proposals import Proposals
from bandteams.settings import CONFIRM_SECONDS


class TestExpiry:
    def test_expired_review_cannot_be_consumed(
        self, journal: Any, person: Any, origin: Any
    ) -> None:
        clock = iter((1000, 1000 + CONFIRM_SECONDS))
        proposals = Proposals(journal, clock=lambda: next(clock))
        proposals.activate(person)
        claim = proposals.stage(person, "room-one", "recipient-one", origin, "body")
        proposals.arm(claim.proposal_id, "review")
        expired = proposals.consume(
            claim.proposal_id, 1, origin.model_copy(update={"activity": "review"})
        )
        assert (expired.delivery, proposals.bodies) == (Delivery.EXPIRED, {})
