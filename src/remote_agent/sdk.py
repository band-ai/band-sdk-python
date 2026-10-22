"""Pure program callbacks; the controller owns persistence and publication."""

from __future__ import annotations

import json
from dataclasses import dataclass

from remote_agent.storage import canonical
from remote_agent.types import JsonObject


@dataclass(frozen=True)
class Message:
    kind: str
    payload: JsonObject


@dataclass(frozen=True)
class Handled:
    state: JsonObject
    message: Message | None = None


@dataclass(frozen=True)
class Delegate:
    reason: str
    scope: str = "turn"
    repair: bool = False


class TurnContext:
    def __init__(self, view: JsonObject) -> None:
        self.view = view
        self.snapshot: JsonObject | None = None

    def checkpoint(self, state: JsonObject) -> None:
        """Stage a snapshot; commit occurs only with this accepted turn."""
        if not isinstance(state, dict):
            raise ValueError("Snapshot state must be a JSON object")
        self.snapshot = json.loads(canonical(state))
