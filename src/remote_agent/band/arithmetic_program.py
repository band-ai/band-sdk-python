"""Full replacement for the pinned bounded-arithmetic calculator role only."""

from __future__ import annotations

from typing import TYPE_CHECKING

from remote_agent.sdk import Delegate, Handled, Message
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.sdk import TurnContext

STATE_VERSION = "1"
COVERAGE = {
    "mode": "full",
    "handles": [
        "add/multiply two integer operands in 0..1000",
        "explicit stop without reply",
    ],
    "delegates": [
        "inputs outside the pinned structured contract; no language interpretation"
    ],
}


def initial(view: JsonObject) -> JsonObject:
    return {"answered": 0}


def handle(
    event: JsonObject, state: JsonObject, context: TurnContext
) -> Handled | Delegate:
    if context.view.get("status") == "terminal" or event.get("kind") in (
        "stop",
        "cancel",
    ):
        return Handled(state)
    payload = event.get("payload", {})
    if (
        event.get("kind") != "question"
        or set(payload) != {"op", "a", "b"}
        or payload["op"] not in ("add", "multiply")
        or any(
            type(payload[name]) is not int or not 0 <= payload[name] <= 1000
            for name in ("a", "b")
        )
    ):
        return Delegate("Input is outside the pinned bounded arithmetic protocol")
    a, b = payload["a"], payload["b"]
    value = a + b if payload["op"] == "add" else a * b
    next_state = {"answered": state["answered"] + 1}
    context.checkpoint(next_state)
    return Handled(
        next_state, Message("answer", {"request": event["id"], "value": value})
    )
