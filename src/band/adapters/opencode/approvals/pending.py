"""The asks OpenCode has put to a room, and the state a reply is matched against."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import OpencodeQuestion
from band.runtime.decisions import DecisionRegistry


@dataclass
class PendingPermission:
    request_id: str
    permission: str
    patterns: list[str]
    activity: ExitStack = field(default_factory=ExitStack)
    activity_tools: AgentToolsProtocol | None = None


@dataclass
class PendingQuestion:
    request_id: str
    questions: list[OpencodeQuestion]
    activity: ExitStack = field(default_factory=ExitStack)
    activity_tools: AgentToolsProtocol | None = None


PendingT = TypeVar("PendingT", PendingPermission, PendingQuestion)


class AskKind(StrEnum):
    """What OpenCode asked the room for."""

    PERMISSION = "permission"
    QUESTION = "question"


class AskState(StrEnum):
    """Where an ask stands."""

    WAITING = "waiting"
    """Still waiting on a human."""
    IN_FLIGHT = "in_flight"
    """A reply to it is being sent to OpenCode."""
    RESOLVED = "resolved"
    """Answered, rejected or expired; no longer held."""


@dataclass(frozen=True)
class NamedAsk:
    """The kind of ask a request id names, and where that ask stands."""

    kind: AskKind
    state: AskState


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
        for entry in (*self.permissions.cancel_all(), *self.questions.cancel_all()):
            entry.payload.activity.close()

    def named_ask(
        self, request_id: str, *, prefer: AskKind = AskKind.PERMISSION
    ) -> NamedAsk | None:
        """The ask ``request_id`` names, or ``None`` if it was never asked here.

        OpenCode prefixes ids per kind, so an id names one kind; if one ever
        named both, the ``prefer`` kind wins.
        """
        order = (prefer, *(kind for kind in AskKind if kind is not prefer))
        held: dict[
            AskKind,
            DecisionRegistry[PendingPermission] | DecisionRegistry[PendingQuestion],
        ] = {
            AskKind.PERMISSION: self.permissions,
            AskKind.QUESTION: self.questions,
        }
        known = {
            AskKind.PERMISSION: self.known_permission_ids,
            AskKind.QUESTION: self.known_question_ids,
        }
        for kind in order:
            registry = held[kind]
            if request_id in registry:
                waiting = registry.is_unclaimed(request_id)
                return NamedAsk(
                    kind, AskState.WAITING if waiting else AskState.IN_FLIGHT
                )
        for kind in order:
            if request_id in known[kind]:
                return NamedAsk(kind, AskState.RESOLVED)
        return None

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
