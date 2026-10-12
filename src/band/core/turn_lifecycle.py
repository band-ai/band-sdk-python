"""One cancellation owner per concrete turn task.

``TurnTask`` delivers cancellation and drains one handler or SDK-managed worker
within a single absolute cleanup grace. Adapters whose manual-approval turn runs
detached bind that worker to the originating turn scope and share
``ApprovalInterruptMixin`` for interrupts and room retirement.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from band.client.streaming import ControlMode
from band.core.exceptions import CleanupIncompleteError
from band.core.retention import task_failure

CYCLE_CANCEL_GRACE_SECONDS = 1.0


def terminal_deadline() -> float:
    """The absolute end of a cleanup grace that starts now."""
    return asyncio.get_running_loop().time() + CYCLE_CANCEL_GRACE_SECONDS


async def finished_within_grace(task: asyncio.Task[Any]) -> bool:
    """Wait up to one cleanup grace for ``task`` without cancelling it."""
    await asyncio.wait({task}, timeout=CYCLE_CANCEL_GRACE_SECONDS)
    return task.done()


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
        self._expiry: asyncio.TimerHandle | None = None
        self._timer: asyncio.Timeout | None = None
        self._cleanup_timer = False

    @staticmethod
    def _retrieve_result(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    def terminate(
        self, *, deadline: float | None = None, cooperative: bool = False
    ) -> None:
        """Start the cleanup grace once; later requests may only shorten it."""
        bound = deadline if deadline is not None else terminal_deadline()
        if self.deadline is not None:
            if bound < self.deadline:
                self._expire_at(bound)
            return
        self._expire_at(self._release_timer(bound))
        if not cooperative:
            self._cancel()

    def _release_timer(self, bound: float) -> float:
        """Keep an attached provider timer from cancelling terminal cleanup."""
        timer = self._timer
        if timer is None:
            return bound
        if self._cleanup_timer and (timer_bound := timer.when()) is not None:
            bound = min(bound, timer_bound)
        if timer.expired():
            self._cancel_delivered = True
        else:
            timer.reschedule(None)
        return bound

    def _expire_at(self, deadline: float) -> None:
        # The owner enforces its own deadline: a detached worker may have no drainer.
        if self._expiry is not None:
            self._expiry.cancel()
        self.deadline = deadline
        self._expiry = asyncio.get_running_loop().call_at(deadline, self._cancel)

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
    """Clear approvals, terminate the handler's worker, and retire its room."""

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    _turn_tasks: dict[str, TurnTask]
    _room_cleanup: dict[str, asyncio.Task[None]]
    _closing_rooms: set[str]

    def _turn_running(self, room_id: str) -> bool:
        owner = self._turn_tasks.get(room_id)
        return owner is not None and not owner.task.done()

    async def _cancel_turn(self, room_id: str) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None:
            owner.terminate()
            await owner.drain()

    async def _retire_room(
        self, room_id: str, close: Callable[[], Awaitable[None]]
    ) -> None:
        """Join the room's one close once its worker finished; retry a failed close."""
        if self._turn_running(room_id):
            raise CleanupIncompleteError(f"Room {room_id}: turn is still cleaning up")
        task = self._room_cleanup.get(room_id)
        if task is None or (task.done() and task_failure(task) is not None):
            task = asyncio.create_task(self._close_room_once(room_id, close))
            task.add_done_callback(task_failure)
            self._room_cleanup[room_id] = task
        await asyncio.shield(task)

    async def _close_room_once(
        self, room_id: str, close: Callable[[], Awaitable[None]]
    ) -> None:
        # Readmission belongs to the close itself, so a cancelled waiter cannot
        # leave the room fenced after the close succeeded.
        await close()
        del self._room_cleanup[room_id]
        self._closing_rooms.discard(room_id)

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
