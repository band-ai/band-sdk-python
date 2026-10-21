"""The asks OpenCode has put to a room, and the state a reply is matched against."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from band.adapters.opencode.approvals.commands import is_question_rejection
from band.integrations.opencode import OpencodeQuestion
from band.runtime.decisions import DecisionRegistry


@dataclass
class PendingPermission:
    request_id: str
    permission: str
    patterns: list[str]


@dataclass
class PendingQuestion:
    request_id: str
    questions: list[OpencodeQuestion]


PendingT = TypeVar("PendingT", PendingPermission, PendingQuestion)


class PendingAsks:
    """Every ask outstanding in one room, and every id ever asked.

    Registries are keyed by request id because OpenCode can have several asks
    outstanding at once (its own clients hold a per-session *list* of pending
    permissions and splice by requestID); a single slot silently dropped the
    earlier ask, whose tool call then blocked server-side until the turn timed
    out. The known-id sets let a reply naming a resolved id still get "no
    longer pending" feedback instead of being forwarded as a fresh prompt; the
    caller may pass room-scoped sets that outlive this instance.
    """

    def __init__(
        self,
        known_permission_ids: set[str] | None = None,
        known_question_ids: set[str] | None = None,
    ) -> None:
        self.permissions: DecisionRegistry[PendingPermission] = DecisionRegistry()
        self.questions: DecisionRegistry[PendingQuestion] = DecisionRegistry()
        self.known_permission_ids = (
            known_permission_ids if known_permission_ids is not None else set()
        )
        self.known_question_ids = (
            known_question_ids if known_question_ids is not None else set()
        )

    def has_claimed(self) -> bool:
        """Whether a reply is in flight for any ask."""
        return self.permissions.has_claimed() or self.questions.has_claimed()

    def parked_on_human(self) -> bool:
        """Whether any ask is still waiting on a human.

        An ask counts as parked until it's claimed -- claiming always cancels
        its expiry timer, so "still parked" and "still unclaimed" coincide.
        """
        return self.permissions.has_unclaimed() or self.questions.has_unclaimed()

    def cancel_all(self) -> None:
        """Drop every ask and stop its expiry timer."""
        self.permissions.cancel_all()
        self.questions.cancel_all()

    def resolve_permission(self, request_id: str | None) -> PendingPermission | None:
        """The ask a reply targets: the named one, else the only one still
        awaiting an answer."""
        if request_id is not None:
            return self.permissions.get(request_id)
        match self.permissions.unclaimed():
            case [only]:
                return only.payload
            case _:
                return None

    def resolve_question(self, command: str) -> PendingQuestion | None:
        """The question a reply targets: the named one, else the oldest pending.

        Free text carries no request id, so it answers the oldest question
        still awaiting an answer -- not one whose reply is already in flight.
        """
        if is_question_rejection(command) and (named := command.split()[1:]):
            return self.questions.get(named[0])
        oldest = self.questions.oldest_unclaimed()
        return None if oldest is None else oldest.payload
