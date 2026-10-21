"""The grammar of a room reply to an OpenCode ask."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from band.integrations.opencode import ApprovalReply


class PermissionReplyWord(StrEnum):
    """The room words that answer a permission ask."""

    APPROVE = "approve"
    ALWAYS = "always"
    REJECT = "reject"


# Keyed for lookup by the raw room word.
PERMISSION_REPLIES: dict[str, ApprovalReply] = {
    PermissionReplyWord.APPROVE: "once",
    PermissionReplyWord.ALWAYS: "always",
    PermissionReplyWord.REJECT: "reject",
}


@dataclass(frozen=True)
class PermissionCommand:
    """A parsed ``approve``/``always``/``reject`` room reply."""

    reply: ApprovalReply
    # None when the user named no request: resolved against the pending ask
    # when exactly one is outstanding.
    request_id: str | None

    @property
    def grants(self) -> bool:
        """Whether the reply approves the ask (once or always)."""
        return self.reply in ("once", "always")

    @property
    def rejects(self) -> bool:
        """Whether the reply declines the ask."""
        return self.reply == "reject"


def parse_permission_reply(content: str) -> PermissionCommand | None:
    """Map a room reply (``approve <id>`` / ``always <id>`` / ``reject <id>``)
    onto the OpenCode reply vocabulary; ``None`` when it is not one of those
    commands."""
    if not (tokens := content.split()):
        return None

    command = tokens[0].lstrip("/").lower()
    trailing = tokens[1:]
    request_id = (
        None
        if not trailing or all(token.lower() == "please" for token in trailing)
        else trailing[0]
    )

    if (reply := PERMISSION_REPLIES.get(command)) is None:
        return None
    return PermissionCommand(reply, request_id)


def parse_question_answers(content: str, question_count: int) -> list[list[str]] | None:
    """One answer line per question; ``None`` when too few lines arrived."""
    if question_count == 0:
        return None
    if question_count == 1:
        answer = content.strip()
        return [[answer]] if answer else None

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < question_count:
        return None
    return [[line] for line in lines[:question_count]]


def is_question_rejection(command: str) -> bool:
    """Whether a room reply rejects a question rather than answering it."""
    parsed = parse_permission_reply(command)
    return parsed is not None and parsed.rejects
