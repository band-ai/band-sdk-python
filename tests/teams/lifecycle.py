"""Generate link and confirmation lifecycle sequences against the real journal."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from bandteams.evidence import Delivery, Journal
from bandteams.proposals import Origin, Proposals
from bandteams.settings import Binding
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule


class Lifecycle(RuleBasedStateMachine):
    def __init__(self, directory: Path, person: Binding, origin: Origin) -> None:
        super().__init__()
        self.person = person
        self.origin = origin
        self.journal = Journal(directory / str(uuid4()))
        self.proposals = Proposals(self.journal)
        self.linked = False
        self.proposal = None
        self.expected = None

    @rule()
    def activate(self) -> None:
        self.proposals.activate(self.person)
        if self.expected == Delivery.REVIEW:
            self.expected = Delivery.CANCELLED
        elif self.expected == Delivery.PENDING:
            self.expected = Delivery.FAILED
        self.linked = True

    @rule()
    def retire(self) -> None:
        self.proposals.retire(self.person.alias)
        if self.expected == Delivery.REVIEW:
            self.expected = Delivery.CANCELLED
        elif self.expected == Delivery.PENDING:
            self.expected = Delivery.FAILED
        self.linked = False

    @precondition(
        lambda self: (
            self.linked and self.expected not in (Delivery.REVIEW, Delivery.PENDING)
        )
    )
    @rule()
    def review(self) -> None:
        origin = self.origin.model_copy(update={"activity": str(uuid4())})
        claim = self.proposals.stage(
            self.person, "room-one", "recipient-one", origin, "body"
        )
        self.proposals.arm(claim.proposal_id, "review")
        self.proposal = claim.proposal_id
        self.expected = Delivery.REVIEW

    @precondition(
        lambda self: (
            self.linked and self.expected in (Delivery.REVIEW, Delivery.PENDING)
        )
    )
    @rule()
    def confirm(self) -> None:
        self.proposals.consume(
            self.proposal, 1, self.origin.model_copy(update={"activity": "review"})
        )
        self.expected = Delivery.PENDING

    @invariant()
    def outcome_matches_link_lifecycle(self) -> None:
        if self.proposal is not None:
            assert self.proposals.claims[self.proposal].delivery == self.expected

    def teardown(self) -> None:
        self.journal.close()
