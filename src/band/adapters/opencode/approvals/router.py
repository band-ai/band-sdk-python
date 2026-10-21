"""Decide which pending ask a room message answers, from state alone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from band.adapters.opencode.approvals.commands import (
    PermissionCommand,
    parse_permission_reply,
)
from band.adapters.opencode.approvals.pending import (
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
    """``reject <id>`` for a question that is no longer pending."""

    request_id: str


ReplyRoute: TypeAlias = (
    PermissionReply | QuestionRejection | QuestionAnswer | NamedQuestionGone
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
    if approval and _permission_command_applies(asks, approval):
        return PermissionReply(approval)

    if (question := asks.resolve_question(command)) is not None:
        if approval is not None and approval.rejects:
            return QuestionRejection(question)
        # Free text: strip only the delivery mention so an answer that
        # legitimately begins with an @handle (naming a person) survives.
        return QuestionAnswer(
            question, strip_leading_mentions(raw, only_first=True).strip()
        )

    if (
        approval is not None
        and approval.rejects
        and approval.request_id is not None
        and (asks.questions or approval.request_id in asks.known_question_ids)
    ):
        return NamedQuestionGone(approval.request_id)

    return None


def _permission_command_applies(asks: PendingAsks, approval: PermissionCommand) -> bool:
    """Whether a parsed approve/always/reject targets a permission.

    ``reject <id>`` is also the question-rejection grammar. A named id
    that is a live or known question (and not a permission) must fall
    through so the question route can run. ``approve`` / ``always`` are not
    shared; those stay on the permission branch so they are not submitted as
    free-text question answers.
    """
    named = approval.request_id
    known_permission = named in asks.known_permission_ids
    known_question = named in asks.known_question_ids
    if (
        named is not None
        and approval.grants
        and named not in asks.permissions
        and not known_permission
        and not known_question
        and asks.questions.has_unclaimed()
    ):
        return False
    if named is not None and approval.rejects and named in asks.questions:
        return False
    if approval.grants:
        return asks.parked_on_human() or (
            named is not None and (known_permission or known_question)
        )
    return asks.permissions.has_unclaimed() or (named is not None and known_permission)
