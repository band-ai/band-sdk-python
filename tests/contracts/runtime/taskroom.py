"""Retain a task request while its first acknowledgement is lost."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar


class UnknownAckRoom:
    actor = "Agent:requester"
    identity: ClassVar[dict[str, str]] = {"source": "pin"}

    def __init__(self, session: str = "s") -> None:
        self.session = session
        self.requests: list[dict[str, Any]] = []
        self.actors: list[str] = []

    def view(self, actor: str) -> dict[str, Any]:
        return {"view": {"version": len(self.requests), "facts": {}}}

    def publish(self, message: dict[str, Any], actor: str) -> dict[str, Any]:
        self.requests.append(deepcopy(message))
        self.actors.append(actor)
        if len(self.requests) == 1:
            raise ValueError("lost acknowledgement")
        return {"status": "duplicate", "original": dict(message, actor=actor)}
