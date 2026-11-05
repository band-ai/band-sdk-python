"""Recover committed delivery metadata without bodies or replay."""

from __future__ import annotations

from typing import Any

import pytest
from bandteams.evidence import Delivery, Journal
from bandteams.proposals import Proposals


class TestRecovery:
    @pytest.mark.parametrize(
        "before,after",
        [
            (Delivery.REVIEW, Delivery.CANCELLED),
            (Delivery.PENDING, Delivery.FAILED),
            (Delivery.SENDING, Delivery.UNKNOWN),
            (Delivery.SENT, Delivery.SENT),
        ],
    )
    def test_restart_preserves_only_safe_outcome(
        self, tmp_path: Any, person: Any, origin: Any, before: Any, after: Any
    ) -> None:
        state = tmp_path / "state"
        first = Journal(state)
        proposals = Proposals(first)
        proposals.activate(person)
        claim = proposals.stage(
            person, "room-one", "recipient-one", origin, "private-body-never-persisted"
        )
        proposals.transition(claim, delivery=before)
        first.close()
        second = Journal(state)
        try:
            recovered = Proposals(second)
            assert recovered.claims[claim.proposal_id].delivery == after
            assert (recovered.bodies, recovered.generations) == ({}, {})
            assert "private-body-never-persisted" not in str(second.records)
        finally:
            second.close()
