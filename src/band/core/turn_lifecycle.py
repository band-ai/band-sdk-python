"""One cancellation owner per concrete turn task, and per backend turn.

``TurnTask`` delivers cancellation and drains one handler or SDK-managed worker
within a single absolute cleanup grace. Adapters whose manual-approval turn runs
detached bind that worker to the originating turn scope and share
``ApprovalInterruptMixin`` for interrupts and room retirement. ``BackendTurns``
is the remote half: it stops the turn an external backend is still running.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from band.client.streaming import ControlMode
from band.core.exceptions import CleanupIncompleteError, InputNotAccepted
from band.core.retention import task_failure
from band.core.retention.cleanup import spawn_owned

CYCLE_CANCEL_GRACE_SECONDS = 1.0
# How long one backend cancel call may run before it is abandoned.
BACKEND_CANCEL_TIMEOUT_SECONDS = 5.0

H = TypeVar("H")


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
        self._grace_expired = False
        self._timer: asyncio.Timeout | None = None
        self._cleanup_timer = False

    @staticmethod
    def _retrieve_result(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    def terminate(
        self, *, deadline: float | None = None, cooperative: bool = False
    ) -> None:
        """Start the cleanup grace once; later requests never renew it."""
        if self.deadline is not None:
            return
        bound = deadline if deadline is not None else terminal_deadline()
        self.deadline = self._release_timer(bound)
        # The owner enforces its own deadline: a detached worker may have no drainer.
        asyncio.get_running_loop().call_at(self.deadline, self._expire)
        if not cooperative:
            self._cancel()

    def retry(self, *, deadline: float) -> None:
        """Cancel again within a fresh grace once the previous grace has expired.

        A task that swallowed its cancel would otherwise stay unfinished on
        every cleanup retry; within an unexpired grace this is ``terminate``.
        """
        if not self._grace_expired:
            self.terminate(deadline=deadline)
            return
        self.deadline = deadline
        self._grace_expired = False
        self._cancel_delivered = False
        asyncio.get_running_loop().call_at(deadline, self._expire)
        self._cancel()

    def _expire(self) -> None:
        # The loop's own timer marks expiry: it can fire up to one clock
        # resolution before loop.time() reaches the deadline.
        self._grace_expired = True
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


@dataclass(eq=False)
class BackendTurn(Generic[H]):
    """One room's backend turn: its handle once known, and whether to stop it."""

    handle: H | None = None
    stop_requested: bool = False


class BackendTurns(Generic[H]):
    """Each room's in-flight backend turn, and the one way to stop it.

    The adapter supplies only the cancel call for its handle; the rules live
    here. A turn is stopped at most once, never by awaiting on the caller, and
    within ``BACKEND_CANCEL_TIMEOUT_SECONDS``. The room's next turn and its
    teardown wait for that stop.

    Placement:

    - The task that owns the backend call holds ``running``, so a turn that
      outlives ``on_message`` (released for approval) stays registered.
    - ``running`` wraps outside any timeout scope: a timeout reaches it as
      ``TimeoutError``, and the adapter's timeout branch calls ``cancel`` then
      awaits ``settled`` before its own cleanup.
    - Where the backend is acquired before the owning task enters ``running``,
      the adapter awaits ``settled`` first.
    - The handle carries everything the cancel needs; the cancel reads no
      ambient context.
    """

    def __init__(
        self, cancel: Callable[[H], Awaitable[None]], *, provider: str
    ) -> None:
        self._cancel = cancel
        self._provider = provider
        self._turns: dict[str, BackendTurn[H]] = {}
        self._stopping: dict[str, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def running(
        self, room_id: str, handle: H | None = None
    ) -> AsyncIterator[None]:
        """Register the room's turn; a cancelled exit stops its backend."""
        await self.settled(room_id)
        turn = BackendTurn(handle)
        self._turns[room_id] = turn
        try:
            yield
        except asyncio.CancelledError:
            self.cancel(room_id)
            raise
        finally:
            if self._turns.get(room_id) is turn:
                del self._turns[room_id]

    def bind(self, room_id: str, handle: H) -> None:
        """Attach the handle the backend revealed; a stop already asked for runs now."""
        turn = self._turns.get(room_id)
        if turn is None or turn.handle is not None:
            return
        turn.handle = handle
        if turn.stop_requested:
            self._stop(room_id, handle)

    def cancel(self, room_id: str) -> None:
        """Start stopping the room's backend turn, once, without waiting for it."""
        turn = self._turns.get(room_id)
        if turn is None or turn.stop_requested:
            return
        turn.stop_requested = True
        if turn.handle is not None:
            self._stop(room_id, turn.handle)

    def stopping(self, room_id: str) -> bool:
        """Whether the room's backend stop is still in flight."""
        return room_id in self._stopping

    async def settled(self, room_id: str, *, timeout: float | None = None) -> None:
        """Wait until the room's backend stop has been sent or timed out, or
        until ``timeout`` passes."""
        if (task := self._stopping.get(room_id)) is not None:
            await asyncio.wait({task}, timeout=timeout)

    async def drain(self) -> None:
        """Wait until every room's backend stop has been sent or timed out."""
        if self._stopping:
            await asyncio.wait(set(self._stopping.values()))

    def _stop(self, room_id: str, handle: H) -> None:
        task = spawn_owned(
            asyncio.wait_for(self._cancel(handle), BACKEND_CANCEL_TIMEOUT_SECONDS),
            label=(
                f"{self._provider} backend stop for room {room_id} "
                f"(limit {BACKEND_CANCEL_TIMEOUT_SECONDS}s)"
            ),
        )
        # The only strong reference until the stop finishes.
        self._stopping[room_id] = task
        task.add_done_callback(lambda done: self._forget_stop(room_id, done))

    def _forget_stop(self, room_id: str, task: asyncio.Task[None]) -> None:
        if self._stopping.get(room_id) is task:
            del self._stopping[room_id]


class ApprovalInterruptMixin:
    """Clear approvals, terminate the handler's worker, and retire its room."""

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    _turn_tasks: dict[str, TurnTask]
    _room_cleanup: dict[str, asyncio.Task[None]]
    _closing_rooms: set[str]

    if TYPE_CHECKING:

        @property
        def backend_turns(self) -> BackendTurns[Any] | None: ...

    def _turn_running(self, room_id: str) -> bool:
        """The room's worker, or the stop of its backend turn, is unfinished."""
        owner = self._turn_tasks.get(room_id)
        if owner is not None and not owner.task.done():
            return True
        return self.backend_turns is not None and self.backend_turns.stopping(room_id)

    async def _admit_turn(self, room_id: str, provider: str) -> None:
        """Refuse new input while the room closes or its turn still runs."""
        if room_id in self._closing_rooms:
            raise InputNotAccepted(f"The room's {provider} session is closing")
        await self._join_backend_stop(room_id)
        if self._turn_running(room_id):
            raise InputNotAccepted(f"The room already has an active {provider} turn")

    async def _join_backend_stop(self, room_id: str) -> None:
        """Give the room's backend stop one cleanup grace to finish."""
        if self.backend_turns is not None:
            await self.backend_turns.settled(
                room_id, timeout=CYCLE_CANCEL_GRACE_SECONDS
            )

    async def _cancel_turn(self, room_id: str) -> None:
        owner = self._turn_tasks.get(room_id)
        if owner is not None:
            owner.terminate()
            await owner.drain()

    async def _retire_room(
        self,
        room_id: str,
        *,
        settle: Callable[[str], Awaitable[None]],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        """Fence the room, end its worker and backend turn, then join the
        room's one close.

        A failed close is retried; only a successful close readmits the room.
        """
        self._closing_rooms.add(room_id)
        self._clear_pending_approvals_for_room(room_id)
        await settle(room_id)
        await self._join_backend_stop(room_id)
        if self._turn_running(room_id):
            raise CleanupIncompleteError(f"Room {room_id}: turn is still cleaning up")
        task = self._room_cleanup.get(room_id)
        if task is None or (task.done() and task_failure(task) is not None):
            task = asyncio.create_task(self._close_room_once(room_id, close))
            task.add_done_callback(task_failure)
            # An eager task factory can finish the close before it is joinable.
            if not task.done():
                self._room_cleanup[room_id] = task
        await asyncio.shield(task)

    async def _close_room_once(
        self, room_id: str, close: Callable[[], Awaitable[None]]
    ) -> None:
        # Readmission belongs to the close itself, so a cancelled waiter cannot
        # leave the room fenced after the close succeeded.
        await close()
        self._room_cleanup.pop(room_id, None)
        self._closing_rooms.discard(room_id)

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        await super().on_interrupt(room_id, mode)  # type: ignore[misc]  # SimpleAdapter follows in the MRO
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
