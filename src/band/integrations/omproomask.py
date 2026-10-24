"""Route native ask dialogs to a room without holding its message queue.

A room processes one message at a time, so a participant's answer cannot enter
an active turn. Acknowledging the posted questions lets that turn finish; the
answer resumes the same native session in a later turn. Cancelling the native
ask would instead abort the whole prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

from band.integrations.ompwire import OmpAskAnswer, OmpAskOption, OmpAskQuestion

DELIVERED_ANSWER = (
    "(Posted to the chat room, where the people in this conversation see it. "
    "Their answer is not available in this turn; it arrives as a later message. "
    "End your turn now: do not answer the question yourself or ask it again.)"
)
DELIVERED_WITH_FIRST_ANSWER = "(Posted to the chat room with the first question.)"
UNDELIVERED_ANSWER = (
    "(The question could not be posted to the chat room: {error}. Proceed "
    "without the answer.)"
)
ROOM_ASK_GUIDANCE = (
    "## Asking the user\n\n"
    "The ask tool posts your questions into the chat room on your behalf. "
    "The answer never arrives inside the same turn; it comes back as a later "
    "message. After calling ask, end your turn and wait for that message."
)


def render_room_questions(questions: Sequence[OmpAskQuestion]) -> str:
    """Render every question, its options and its recommended choice together."""
    numbered = len(questions) > 1
    return "\n\n".join(
        _render_question(question, number if numbered else None)
        for number, question in enumerate(questions, 1)
    )


def _render_question(question: OmpAskQuestion, number: int | None) -> str:
    heading = question.question.strip()
    header = (question.header or "").strip()
    if header:
        heading = f"**{header}:** {heading}"
    if number is not None:
        heading = f"{number}. {heading}"
    indent = "   " if number is not None else ""
    lines = [heading]
    lines.extend(
        f"{indent}- {_render_option(option, recommended=index == question.recommended)}"
        for index, option in enumerate(question.options)
    )
    if question.multi and question.options:
        lines.append(f"{indent}Pick any that apply.")
    return "\n".join(lines)


def _render_option(option: OmpAskOption, *, recommended: bool) -> str:
    text = option.label
    description = (option.description or "").strip()
    if description:
        text += f": {description}"
    if recommended:
        text += " (recommended)"
    return text


def delivered_answers(questions: Sequence[OmpAskQuestion]) -> list[OmpAskAnswer]:
    """Acknowledge each question in the order required by the native protocol."""
    return [
        OmpAskAnswer(
            id=question.id,
            customInput=DELIVERED_ANSWER if index == 0 else DELIVERED_WITH_FIRST_ANSWER,
        )
        for index, question in enumerate(questions)
    ]


def undelivered_answers(
    questions: Sequence[OmpAskQuestion], error: BaseException
) -> list[OmpAskAnswer]:
    """Report a failed post without cancelling the native prompt."""
    text = UNDELIVERED_ANSWER.format(error=error)
    return [OmpAskAnswer(id=question.id, customInput=text) for question in questions]
