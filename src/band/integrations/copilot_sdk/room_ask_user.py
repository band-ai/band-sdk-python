"""Room routing for Copilot's ``ask_user`` tool.

``CopilotSDKAdapterConfig(ask_user="room")`` bridges the model's built-in
``ask_user`` tool to the Band room itself: the question is posted as a
room message (mentioning whoever triggered the turn) and the tool call
resolves immediately with :data:`QUESTION_DELIVERED_ANSWER`, so the turn
ends and the user's reply arrives as the *next* message on the same
persisted Copilot session.

That turn-split is forced by how the two runtimes work, not a styling
choice:

- **Band** processes each room's messages strictly one at a time
  (``ExecutionContext._process_loop``). A handler that blocked the turn
  waiting for the user's reply could never receive it — the reply sits
  in the room queue until the turn it is supposed to unblock finishes.
- **Copilot** services ``ask_user`` as a ``userInput.request`` JSON-RPC
  call that stays pending until answered: no timeout, no cancellation
  on abort, and the pending state is ephemeral — it is not replayed
  when a session is resumed, so an answer "owed" across a restart is
  simply lost.

Answering immediately with a delivery acknowledgement is the only shape
that survives both: the room stays responsive, the turn cannot dead-hang
against ``turn_timeout_s``, and the pending question lives where it is
actually durable — in the session's own conversation history.

The module depends only on the wire *shape* of the Copilot SDK's
``UserInputRequest``/``UserInputResponse`` TypedDicts (plain dicts at
runtime), so it imports nothing from ``copilot`` at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from copilot import UserInputResponse

ASK_USER_ROOM = "room"

# Echoes the rendered question so the model knows exactly what the user
# sees — e.g. that a bare-number answer refers to the numbered choices.
QUESTION_DELIVERED_ANSWER = (
    "(Your question was posted to the chat room as shown below, and the "
    "user was notified. Their answer is NOT available in this turn — it "
    "will arrive as a later user message; a bare number picks the matching "
    "numbered option. End your turn now: do not repeat the question, do "
    "not answer it yourself, and do not call ask_user again until their "
    "answer arrives.\n---\n{rendered}\n---)"
)

ROOM_INACTIVE_ANSWER = (
    "(The chat room is no longer active, so the question could not be "
    "delivered. Proceed without the user's input.)"
)

DELIVERY_FAILED_ANSWER = (
    "(The question could not be delivered to the chat room: {error}. "
    "Proceed without the user's input, or try again later.)"
)

# Injected into the system prompt (as its own section, not developer
# text) so the model learns the turn-split contract before its first
# ask, not from the first tool result.
ROOM_ASK_USER_GUIDANCE = (
    "## Asking the user\n\n"
    "When you need an answer, a decision, or an approval from the people "
    "in this conversation, call the ask_user tool — offer choices when "
    "they exist. The question is posted into the chat room on your "
    "behalf; the answer never arrives inside the same turn, it comes "
    "back as a later user message. After calling ask_user, end your turn "
    "and wait for that message."
)


def freeform_answer(text: str) -> UserInputResponse:
    """Wrap text as the ask_user tool's freeform-answer response shape."""
    return {"answer": text, "wasFreeform": True}


# Composed answers: template text and placeholder names stay inside this
# module; the adapter only ever calls these.


def question_delivered_answer(rendered_question: str) -> UserInputResponse:
    """The ack for a question that posted: echoes what the user sees."""
    return freeform_answer(QUESTION_DELIVERED_ANSWER.format(rendered=rendered_question))


def delivery_failed_answer(error: Exception) -> UserInputResponse:
    """The answer when posting the question to the room failed."""
    return freeform_answer(DELIVERY_FAILED_ANSWER.format(error=error))


def room_inactive_answer() -> UserInputResponse:
    """The answer for a dispatch whose room/turn is already gone."""
    return freeform_answer(ROOM_INACTIVE_ANSWER)


def render_room_question(request: Mapping[str, Any]) -> str:
    """Render an ``ask_user`` request as a room-readable message.

    Choices become a numbered list so a bare-number reply maps cleanly
    back to one option; the trailing hint tells the user which reply
    shapes the request accepts.
    """
    question = str(request.get("question") or "").strip()
    choices = [str(choice) for choice in request.get("choices") or []]
    if not choices:
        return question
    numbered = "\n".join(f"{i}. {choice}" for i, choice in enumerate(choices, 1))
    hint = (
        "Reply with a number or your own answer."
        if request.get("allowFreeform", True)
        else "Reply with the number of one of the options."
    )
    return f"{question}\n\n{numbered}\n\n{hint}"
