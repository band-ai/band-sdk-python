"""Shared ``on_interrupt`` for adapters whose manual-approval turn runs detached.

``ExecutionContext.interrupt()``/``stop_room()`` only cancel the task that
invoked ``on_message`` -- once that returns early because a turn released the
room to await a human's approval reply, only the adapter itself can still
abort the detached turn. See ``SimpleAdapter.on_interrupt``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band.client.streaming import ControlMode

CYCLE_CANCEL_GRACE_SECONDS = 1.0


class TurnTask:
    """Own cancellation and bounded draining for one concrete turn task."""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self.task = task
        if task.done():
            self._retrieve_result(task)
        else:
            task.add_done_callback(self._retrieve_result)
        self.deadline: float | None = None
        self._cancel_delivered = False
        self._timer: asyncio.Timeout | None = None
        self._cleanup_timer = False

    @staticmethod
    def _retrieve_result(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    def terminate(
        self, *, deadline: float | None = None, cooperative: bool = False
    ) -> None:
        bound = (
            deadline
            if deadline is not None
            else (asyncio.get_running_loop().time() + CYCLE_CANCEL_GRACE_SECONDS)
        )
        if self.deadline is not None:
            self.deadline = min(self.deadline, bound)
            return
        timer = self._timer
        if timer is not None:
            if self._cleanup_timer and (timer_bound := timer.when()) is not None:
                bound = min(bound, timer_bound)
            if timer.expired():
                self._cancel_delivered = True
            else:
                timer.reschedule(None)
        self.deadline = bound
        if not cooperative:
            self._cancel()

    def _cancel(self) -> None:
        if not self._cancel_delivered and not self.task.done():
            self._cancel_delivered = True
            self.task.cancel()

    async def drain(self) -> bool:
        """Wait within the original terminal grace without cancelling a waiter."""
        if self.task.done():
            return True
        if self.deadline is None:
            raise RuntimeError("Turn draining requires termination")
        remaining = max(0.0, self.deadline - asyncio.get_running_loop().time())
        await asyncio.wait({self.task}, timeout=remaining)
        if not self.task.done():
            self._cancel()
        return self.task.done()

    @asynccontextmanager
    async def timed(
        self, timeout: float | None, *, cleanup: bool = False
    ) -> AsyncIterator[asyncio.Timeout]:
        """Coordinate an existing provider timer with terminal cancellation."""
        if self.deadline is not None:
            raise asyncio.CancelledError
        if self._timer is not None or asyncio.current_task() is not self.task:
            raise RuntimeError("A turn timer must run in its owner, without nesting")
        timer = asyncio.timeout(timeout)
        try:
            async with timer:
                self._timer = timer
                self._cleanup_timer = cleanup
                yield timer
        finally:
            self._timer = None
            self._cleanup_timer = False
            if self.deadline is not None:
                raise asyncio.CancelledError


def bind_turn_task(tools: object, task: asyncio.Task[None]) -> TurnTask:
    """Bind SDK-owned work when tools expose a runtime scope."""
    owner = TurnTask(task)
    register = getattr(tools, "register_turn_task", None)
    if callable(register):
        register(owner)
    return owner


class ApprovalInterruptMixin:
    """Clear approvals and terminate the same worker owned by the handler."""

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    _turn_tasks: dict[str, TurnTask]

    async def _cancel_turn(self, room_id: str) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None:
            owner.terminate()
            await owner.drain()

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
