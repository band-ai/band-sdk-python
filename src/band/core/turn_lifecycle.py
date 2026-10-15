"""One cancellation owner per concrete turn task.

``TurnTask`` delivers cancellation and drains one handler or SDK-managed worker
within a single absolute cleanup grace. Adapters whose manual-approval turn runs
detached bind that worker to the originating turn scope and share
``ApprovalInterruptMixin`` for interrupts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Annotated, Any

from pydantic import Field, NonNegativeFloat

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.retention import RetainedRoom
from band.core.turn import judge_detached_turn

CYCLE_CANCEL_GRACE_SECONDS = 1.0
# How long a room's final disposal lets its accepted turn wind down on its own
# before cancelling it.
TURN_SETTLE_GRACE_S = 10.0
# The adapter config field that sets a room's settle grace. Each config assigns
# TURN_SETTLE_GRACE_S itself: type checkers miss a default inside Annotated.
TurnSettleTimeout = Annotated[
    NonNegativeFloat,
    Field(
        description=(
            "Seconds a leave or stop lets the room's turn wind down on its own "
            "before cancelling it."
        ),
    ),
]


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
        self._cancel_baseline = task.cancelling()
        self.deadline: float | None = None
        self._cancel_delivered = False
        self._grace_expired = False
        self._timer: asyncio.Timeout | None = None
        self._cleanup_timer = False
        self._expiry_timer: asyncio.TimerHandle | None = None
        self._closed = False
        if task.done():
            self._finish(task)
        else:
            task.add_done_callback(self._finish)

    @staticmethod
    def _retrieve_result(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    def _finish(self, task: asyncio.Task[None]) -> None:
        self.close()
        self._retrieve_result(task)

    def close(self) -> None:
        """End this owner's lifetime without cancelling its task."""
        if self._closed:
            return
        self._closed = True
        if self._expiry_timer is not None:
            self._expiry_timer.cancel()
            self._expiry_timer = None
        if self._timer is not None and not self._timer.expired():
            self._timer.reschedule(None)
        self.task.remove_done_callback(self._finish)

    def _schedule_expiry(self, deadline: float) -> None:
        if self._expiry_timer is not None:
            self._expiry_timer.cancel()
        self._expiry_timer = asyncio.get_running_loop().call_at(deadline, self._expire)

    def terminate(
        self, *, deadline: float | None = None, cooperative: bool = False
    ) -> None:
        """Start the cleanup grace once; later requests never renew it."""
        if self._closed:
            return
        self._adopt_cancellation()
        if self.deadline is not None:
            return
        bound = deadline if deadline is not None else terminal_deadline()
        self.deadline = self._release_timer(bound)
        # The owner enforces its own deadline: a detached worker may have no drainer.
        self._schedule_expiry(self.deadline)
        if not cooperative:
            self._cancel()

    def retry(self, *, deadline: float) -> None:
        """Cancel again within a fresh grace once the previous grace has expired.

        A task that swallowed its cancel would otherwise stay unfinished on
        every cleanup retry; within an unexpired grace this is ``terminate``.
        """
        if self._closed:
            return
        if not self._grace_expired:
            self.terminate(deadline=deadline)
            return
        self.deadline = deadline
        self._grace_expired = False
        self._cancel_delivered = False
        self._schedule_expiry(deadline)
        self._cancel()

    def _expire(self) -> None:
        if self._closed:
            return
        # The loop's own timer marks expiry: it can fire up to one clock
        # resolution before loop.time() reaches the deadline.
        self._grace_expired = True
        self._adopt_cancellation()
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

    def _adopt_cancellation(self) -> None:
        # Counts predating this owner belong to a previous cancellation lifetime.
        if self.task.cancelling() > self._cancel_baseline:
            self._cancel_delivered = True

    def _cancel(self) -> None:
        if not self._closed and not self._cancel_delivered and not self.task.done():
            self._cancel_delivered = True
            self.task.cancel()

    async def drain(self) -> bool:
        """Wait within the original terminal grace without cancelling a waiter."""
        if self.task.done():
            return True
        if self._closed:
            return False
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
        if self._closed or self.deadline is not None:
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


def start_releasable_turn(
    retained: RetainedRoom,
    operation: Callable[[], Coroutine[Any, Any, None]],
    *,
    tools: AgentToolsProtocol,
    room_id: str,
) -> asyncio.Task[None]:
    """Start a turn that may be released before it ends as the room's owned work.

    A released turn cancelled by its room's final disposal is judged then,
    since nothing else answers for its message; an interrupt ends it on purpose.
    """

    async def run() -> None:
        try:
            await operation()
        except asyncio.CancelledError:
            if retained.closing:
                await judge_detached_turn(tools, room_id=room_id)
            raise

    return retained.start_owned(run)


def bind_turn_task(tools: object, task: asyncio.Task[None]) -> TurnTask:
    """Bind SDK-owned work when tools expose a runtime scope."""
    owner = TurnTask(task)
    register = getattr(tools, "register_turn_task", None)
    if callable(register):
        register(owner)
    return owner


class ApprovalInterruptMixin:
    """Clear approvals and terminate the handler's worker on an interrupt.

    A room's leave and an adapter's stop belong to its residency owner, which
    refuses a closing room's work itself.
    """

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    _turn_tasks: dict[str, TurnTask]

    def _turn_running(self, room_id: str) -> bool:
        owner = self._turn_tasks.get(room_id)
        return owner is not None and not owner.task.done()

    def _admit_turn(self, room_id: str, provider: str) -> None:
        """Refuse new input while the room's worker still runs."""
        if self._turn_running(room_id):
            raise InputNotAccepted(f"The room already has an active {provider} turn")

    async def _cancel_turn(self, room_id: str) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None:
            owner.terminate()
            await owner.drain()

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
