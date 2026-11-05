"""Doubles for unaccepted work sharing the host scheduler with active-turn controls."""

from __future__ import annotations

import asyncio
from typing import Any

from band.core.exceptions import InputNotAccepted
from band.core.simple_adapter import SimpleAdapter
from band.core.turn_lifecycle import BackendTurns, bind_turn_task
from tests.runtime.helpers import ROOM_ID


def complete_blocker(blocker: asyncio.Future[None]) -> None:
    blocker.set_result(None)


def cancel_blocker(blocker: asyncio.Future[None]) -> None:
    blocker.cancel()


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


class EndsWhileRefusingAdapter(SimpleAdapter[Any]):
    """A released turn that ends while the room is still refusing the next input."""

    def __init__(self) -> None:
        super().__init__()
        self.completed: list[str] = []
        self._finish = asyncio.Event()
        self._released: asyncio.Task[None] | None = None

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        released = self._released
        if released is None:
            tools.turn.detach()
            self._released = asyncio.create_task(self._finish_turn(msg.id, tools))
            bind_turn_task(tools, self._released)
            return
        if not released.done():
            self._finish.set()
            await asyncio.wait({released})
            raise InputNotAccepted()
        self.completed.append(msg.id)
        tools.turn.settle()

    async def _finish_turn(self, message_id: str, tools: Any) -> None:
        await self._finish.wait()
        self.completed.append(message_id)
        tools.turn.settle()


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


class ReadinessAdapter(SimpleAdapter[Any]):
    """Refuse input against externally controlled, successive backend blockers."""

    def __init__(self, *blockers: asyncio.Future[None]) -> None:
        super().__init__()
        self.blockers = list(blockers)
        self.attempts = 0
        self.completed: list[str] = []
        self.refused = asyncio.Event()

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        self.attempts += 1
        blocker = next((item for item in self.blockers if not item.done()), None)
        if blocker is not None:
            self.refused.set()
            raise InputNotAccepted(ready=blocker)
        self.completed.append(msg.id)
        tools.turn.settle()


class SettledRefusalAdapter(SimpleAdapter[Any]):
    """A custom adapter repeatedly returns the same already-complete blocker."""

    def __init__(self, blocker: asyncio.Future[None]) -> None:
        super().__init__()
        self.blocker = blocker
        self.attempts = 0
        self.attempted: asyncio.Queue[None] = asyncio.Queue()

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        self.attempts += 1
        self.attempted.put_nowait(None)
        raise InputNotAccepted(ready=self.blocker)


class GatedRefusalAdapter(SimpleAdapter[Any]):
    """Hold admission until shutdown has begun, then refuse without executing."""

    def __init__(self, blocker: asyncio.Future[None]) -> None:
        super().__init__()
        self.blocker = blocker
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        self.entered.set()
        await self.release.wait()
        raise InputNotAccepted(ready=self.blocker)


class RecoveringAdapter(SimpleAdapter[Any]):
    """Admit through the real backend owner before executing the input."""

    def __init__(self, turns: BackendTurns[object]) -> None:
        super().__init__()
        self.turns = turns
        self.refused = asyncio.Event()
        self.completed: list[str] = []

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        try:
            await self.turns.require_admission(ROOM_ID, tools)
        except InputNotAccepted:
            self.refused.set()
            raise
        self.completed.append(msg.id)
        tools.turn.settle()


async def callbacks_finished() -> None:
    """Pass the callbacks already queued on the event loop."""
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await reached.wait()
