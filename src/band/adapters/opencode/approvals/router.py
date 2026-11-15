"""Decide which pending ask a room message answers, from state alone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from band.adapters.opencode.approvals.commands import (
    PermissionCommand,
    parse_permission_reply,
)
from band.adapters.opencode.approvals.pending import (
    AskKind,
    AskState,
    NamedAsk,
    PendingAsks,
    PendingQuestion,
)
from band.runtime.formatters import strip_leading_mentions


@dataclass(frozen=True)
class PermissionReply:
    """A room reply that answers a permission ask."""

    approval: PermissionCommand


@dataclass(frozen=True)
class QuestionRejection:
    """A room reply that rejects ``question``."""

    question: PendingQuestion


@dataclass(frozen=True)
class QuestionAnswer:
    """A room reply that is free text for ``question``, mention already stripped."""

    question: PendingQuestion
    text: str


@dataclass(frozen=True)
class NamedQuestionGone:
    """A named reply for a question that no longer waits on one.

    Also an unrecognized ``reject <id>`` while a question is held.
    """

    request_id: str


@dataclass(frozen=True)
class NamedQuestionGrant:
    """``approve``/``always`` naming a question still waiting for its answer."""

    request_id: str


ReplyRoute: TypeAlias = (
    PermissionReply
    | QuestionRejection
    | QuestionAnswer
    | NamedQuestionGone
    | NamedQuestionGrant
)


def route_reply(asks: PendingAsks, content: str) -> ReplyRoute | None:
    """Where a room message goes, decided from the pending state alone.

    The one parser behind recognition and handling; it has no side effects.
    ``None`` means the message is not a reply to any ask.
    """
    if not (raw := content.strip()):
        return None
    # A command/keyword never *is* an @mention, so skip the whole leading
    # mention block to find it (robust to several leading mentions).
    command = strip_leading_mentions(raw).strip()

    approval = parse_permission_reply(command)
    if approval is not None:
        # A reject is the one command a question takes by id.
        prefer = AskKind.QUESTION if approval.rejects else AskKind.PERMISSION
        if approval.request_id is not None and (
            named := asks.named_ask(approval.request_id, prefer=prefer)
        ):
            return _route_named(asks, approval, approval.request_id, named)
        if _permission_command_applies(asks, approval):
            return PermissionReply(approval)
        if approval.rejects and approval.request_id is not None:
            # An unrecognized id: feedback while a question is held, not a
            # fresh prompt for the model.
            return NamedQuestionGone(approval.request_id) if asks.questions else None

    if (oldest := asks.questions.oldest_unclaimed()) is None:
        return None
    if approval is not None and approval.rejects:
        return QuestionRejection(oldest.payload)
    # Free text answers the oldest question still waiting. Strip only the
    # delivery mention so an answer that begins with an @handle survives.
    return QuestionAnswer(
        oldest.payload, strip_leading_mentions(raw, only_first=True).strip()
    )


def _route_named(
    asks: PendingAsks, approval: PermissionCommand, request_id: str, named: NamedAsk
) -> ReplyRoute:
    """Route a reply by the kind of ask its id names, not by its word."""
    if named.kind is AskKind.PERMISSION:
        return PermissionReply(approval)
    if named.state is AskState.RESOLVED:
        return NamedQuestionGone(request_id)
    if approval.rejects:
        return QuestionRejection(asks.questions[request_id])
    if named.state is AskState.WAITING:
        return NamedQuestionGrant(request_id)
    return NamedQuestionGone(request_id)


def _permission_command_applies(asks: PendingAsks, approval: PermissionCommand) -> bool:
    """Whether a bare or unrecognized approve/always/reject targets a permission.

    An unrecognized id after ``approve``/``always`` while a question waits is
    that question's free-text answer.
    """
    if approval.grants:
        if approval.request_id is not None and asks.questions.has_unclaimed():
            return False
        return asks.parked_on_human()
    return asks.permissions.has_unclaimed()
