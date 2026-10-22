"""Deliberately faulty fixture: the exact 'recover' case sends the wrong type."""

from __future__ import annotations

from remote_agent.sdk import Delegate, Handled, Message, TurnContext
from remote_agent.types import JsonObject

STATE_VERSION = "1"
COVERAGE = {
    "mode": "partial",
    "handles": ["exact ping", "exact recover"],
    "delegates": ["all other text; free-form language requires judgment"],
}


def initial(view: JsonObject) -> JsonObject:
    return {"count": 0}


def handle(
    event: JsonObject, state: JsonObject, context: TurnContext
) -> Handled | Delegate:
    if event["kind"] == "stop":
        return Handled(state)
    text = event["payload"]["text"]
    if text not in ("ping", "recover"):
        return Delegate("Free-form language requires model judgment")
    state = {"count": state["count"] + 1}
    context.checkpoint(state)
    if text == "recover":
        # Fault injection for the demo. A generated candidate corrects this one type.
        return Handled(
            state, Message("question", {"request": event["id"], "text": "recovered"})
        )
    return Handled(state, Message("answer", {"request": event["id"], "text": "pong"}))
