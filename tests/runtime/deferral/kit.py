"""Doubles for unaccepted work sharing the host scheduler with active-turn controls."""

from __future__ import annotations

from typing import Any

from band.core.exceptions import InputNotAccepted
from band.core.simple_adapter import SimpleAdapter
from tests.runtime.helpers import ROOM_ID


class ApprovalAdapter(SimpleAdapter[Any]):
    """A detached turn owns its tools until its approval is consumed."""

    def __init__(self) -> None:
        super().__init__()
        self.active_tools: Any = None
        self.completed: list[str] = []
        self.attempted: list[str] = []
        self.controls: list[str] = []
        self.reject = False

    def is_control_message(self, msg: Any) -> bool:
        return msg.content == "approve"

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        if msg.content != "approve":
            return False
        self.controls.append(msg.id)
        if self.active_tools is not None:
            self.active_tools.turn.settle()
            self.completed.append("A")
            self.active_tools = None
        return True

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        self.attempted.append(msg.id)
        if self.active_tools is not None or self.reject:
            raise InputNotAccepted()
        if msg.id == "A":
            tools.turn.detach()
            self.active_tools = tools
        else:
            self.completed.append(msg.id)
            tools.turn.settle()


class FailureThenBusyAdapter(ApprovalAdapter):
    """An execution failure makes its next attempts temporarily unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.busy = False
        self.busy_attempts = 0
        self.failure_attempts = 0

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        if self.busy:
            self.busy_attempts += 1
            raise InputNotAccepted()
        self.failure_attempts += 1
        self.busy = True
        raise OSError("provider transport uncertainty")


def envelope(message_id: str) -> dict[str, Any]:
    return {
        "event_type": "message_created",
        "room_id": ROOM_ID,
        "payload": {
            "id": message_id,
            "content": "approve" if message_id == "C" else "ordinary",
            "sender_id": "user-1",
            "sender_type": "User",
            "message_type": "text",
            "inserted_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    }
