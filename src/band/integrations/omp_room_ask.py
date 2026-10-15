"""Room routing for OMP's built-in ``ask`` tool.

``OmpAdapter`` opts every session into OMP's structured ``ask`` dialog
(``set_ask_dialog``) and answers each dialog by posting its questions to the
Band room, mentioning whoever triggered the turn. Every question is answered
without waiting for the participant, with an acknowledgement in
``customInput`` that tells the model to end its turn; the reply arrives as
the next message on the same persisted OMP session.

That turn split is forced by both runtimes:

- **Band** processes a room's messages one at a time, so a turn that waited
  for the reply could never receive it: the reply sits in the room queue
  until the waiting turn ends.
- **OMP** treats a cancelled ``ask`` as the user interrupting the turn and
  aborts the whole prompt, so declining the dialog fails the message.
"""

from __future__ import annotations

from collections.abc import Sequence

from band.integrations.ompwire import OmpAskAnswer, OmpAskQuestion

DELIVERED_ANSWER = (
    "(Posted to the chat room, where the people in this conversation see it. "
    "Their answer is not available in this turn; it arrives as a later "
    "message. End your turn now: do not answer the question yourself or ask "
    "it again.)"
)

DELIVERED_WITH_FIRST_ANSWER = "(Posted to the chat room with the first question.)"

UNDELIVERED_ANSWER = (
    "(The question could not be posted to the chat room: {error}. Proceed "
    "without the answer.)"
)

# Part of the system prompt, so the model learns the turn split before its
# first ask rather than from the first tool result.
ROOM_ASK_GUIDANCE = (
    "## Asking the user\n\n"
    "The ask tool posts your questions into the chat room on your behalf. "
    "The answer never arrives inside the same turn; it comes back as a later "
    "message. After calling ask, end your turn and wait for that message."
)


def render_room_questions(questions: Sequence[OmpAskQuestion]) -> str:
    """Render an ``ask`` dialog as one room message: each question with its
    header, then its options with descriptions and the recommended choice."""
    numbered = len(questions) > 1
    blocks: list[str] = []
    for number, question in enumerate(questions, 1):
        heading = question.question.strip()
        if question.header and question.header.strip():
            heading = f"**{question.header.strip()}:** {heading}"
        if numbered:
            heading = f"{number}. {heading}"
        indent = "   " if numbered else ""
        lines = [heading]
        for index, option in enumerate(question.options):
            line = f"{indent}- {option.label}"
            if option.description and option.description.strip():
                line += f": {option.description.strip()}"
            if index == question.recommended:
                line += " (recommended)"
            lines.append(line)
        if question.multi and question.options:
            lines.append(f"{indent}Pick any that apply.")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def delivered_answers(questions: Sequence[OmpAskQuestion]) -> list[OmpAskAnswer]:
    """One pending answer per question, in question order, as OMP requires."""
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
    """Tell the model the post failed instead of cancelling, which would
    abort its turn."""
    text = UNDELIVERED_ANSWER.format(error=error)
    return [OmpAskAnswer(id=question.id, customInput=text) for question in questions]
