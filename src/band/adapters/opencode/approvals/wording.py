"""Every sentence an OpenCode approval puts in the room or in front of the model.

A consumer that has to recognize these lines (the E2E smoke waiting for a real
permission round trip) matches one definition instead of re-typing the sentence.
"""

from __future__ import annotations

from band.adapters.opencode.approvals.commands import PermissionReplyWord
from band.adapters.opencode.approvals.pending import (
    PendingPermission,
    PendingQuestion,
    PendingT,
)
from band.integrations.opencode import OpencodeQuestion
from band.runtime.decisions import DecisionRegistry
from band.runtime.formatters import format_tokens

APPROVAL_REQUESTED_TEMPLATE = (
    "OpenCode approval requested for `{permission}` ({patterns}). Reply with "
    f"`{PermissionReplyWord.APPROVE} {{request_id}}`, "
    f"`{PermissionReplyWord.ALWAYS} {{request_id}}`, or "
    f"`{PermissionReplyWord.REJECT} {{request_id}}`."
)
APPROVAL_HANDLED_TEMPLATE = "OpenCode approval `{request_id}` handled with `{reply}`."
APPROVAL_TIMED_OUT_TEMPLATE = (
    "OpenCode approval `{request_id}` timed out and was handled with `{reply}`."
)
APPROVAL_NO_LONGER_PENDING_TEMPLATE = (
    "OpenCode approval `{request_id}` is no longer pending."
)
QUESTION_NO_LONGER_PENDING_TEMPLATE = (
    "OpenCode question `{request_id}` is no longer pending."
)
QUESTION_ANSWERED_TEMPLATE = "OpenCode question `{request_id}` answered."
QUESTION_REJECTED_TEMPLATE = "OpenCode question `{request_id}` rejected."
QUESTION_TIMED_OUT_TEMPLATE = (
    "OpenCode question `{request_id}` timed out and was rejected."
)
QUESTION_ANSWERS_NEEDED = (
    "OpenCode is waiting for answers. Reply with one line per "
    "question, or `reject` to reject the question."
)
# Feedback carried on POST /permission/{id}/reply so OpenCode hands the turn
# back to the model instead of ending on a bare RejectedError.
REJECTED_PERMISSION_FEEDBACK = (
    "This request was declined. Do not retry it or try another way to do the "
    "same thing; reply to the user instead."
)
# A declined question is answered with this, one line per question, never sent
# to POST /question/{id}/reject: that route carries no feedback, so OpenCode
# ends the turn on it and the model never replies to the room.
DECLINED_QUESTION_ANSWER = (
    "This question was declined. Do not ask it again; continue and reply to "
    "the user without it."
)


def format_question_prompt(questions: list[OpencodeQuestion], request_id: str) -> str:
    prompt_lines = [f"OpenCode asked question `{request_id}`:"]
    for index, question in enumerate(questions, start=1):
        prompt_lines.append(f"{index}. {question.question}")
    prompt_lines.append("Reply with one line per question, or `reject`.")
    return "\n".join(prompt_lines)


def which_permission_hint(permissions: DecisionRegistry[PendingPermission]) -> str:
    return (
        f"Several OpenCode approvals are pending ({_format_ids(permissions)}). "
        f"Reply with the request id, e.g. `{PermissionReplyWord.APPROVE} <id>`."
    )


def which_question_command_hint(questions: DecisionRegistry[PendingQuestion]) -> str:
    return (
        f"OpenCode is waiting for question answers ({_format_ids(questions)}). "
        f"Reply with your answer or `{PermissionReplyWord.REJECT} <id>` — not "
        f"`{PermissionReplyWord.APPROVE}`/`{PermissionReplyWord.ALWAYS}`."
    )


def which_dual_reject_hint(
    permissions: DecisionRegistry[PendingPermission],
    questions: DecisionRegistry[PendingQuestion],
) -> str:
    return (
        "Both an approval and a question are pending "
        f"({_format_ids(permissions)}; {_format_ids(questions)}). Reply with "
        f"`{PermissionReplyWord.REJECT} <id>` naming "
        "which ask to reject."
    )


def _format_ids(registry: DecisionRegistry[PendingT]) -> str:
    """The asks still awaiting an answer, as a hint's id list."""
    return format_tokens(entry.token for entry in registry.unclaimed())
