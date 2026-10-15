"""One cancellation owner per concrete turn task, and per backend turn.

``TurnTask`` delivers cancellation and drains one handler or SDK-managed worker
within a single absolute cleanup grace. Adapters whose manual-approval turn runs
detached bind that worker to the originating turn scope and share
``ApprovalInterruptMixin`` for interrupts. ``BackendTurns`` is the remote half:
it stops the turn an external backend is still running.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Generic, TypeVar

from pydantic import Field, NonNegativeFloat

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.retention import RetainedRoom
from band.core.retention.cleanup import spawn_owned
from band.core.turn import judge_detached_turn

logger = logging.getLogger(__name__)

CYCLE_CANCEL_GRACE_SECONDS = 1.0
# How long one backend stop may run, including any wait for the start call
# whose turn it stops, before it is abandoned.
BACKEND_CANCEL_TIMEOUT_SECONDS = 5.0
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

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self.task = task
        self._cancel_baseline = task.cancelling()
        self.deadline: float | None = None
        self._cancel_delivered = False
        self._cancel_reason: str | None = None
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
    def _retrieve_result(task: asyncio.Task[Any]) -> None:
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
        self,
        *,
        deadline: float | None = None,
        cooperative: bool = False,
        reason: str | None = None,
    ) -> None:
        """Start the cleanup grace once; later requests never renew it.

        Keep the first supplied reason for delivery, including cooperative expiry.
        """
        if self._closed:
            return
        if self._cancel_reason is None:
            self._cancel_reason = reason
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
            self.task.cancel(self._cancel_reason)

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


def bind_turn_task(tools: object, task: asyncio.Task[Any]) -> TurnTask:
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
      ``TimeoutError``, and the adapter's timeout branch awaits ``stop``
      before its own cleanup.
    - Where the backend is acquired before the owning task enters ``running``,
      the adapter awaits ``settled`` first.
    - Where the call that starts the turn also reveals its handle, the owner
      awaits it through ``started``, so a stop asked for mid-call still lands.
    - The handle carries the turn's own state the cancel needs; the cancel
      never reads per-turn ambient context such as a ``ContextVar``.
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
            if (
                turn.stop_requested
                and turn.handle is None
                and not self.stopping(room_id)
            ):
                logger.info(
                    "%s turn in room %s ended before its backend revealed it; "
                    "no backend stop was sent",
                    self._provider,
                    room_id,
                )

    async def started(self, room_id: str, start: Awaitable[H | None]) -> H | None:
        """Await the call that starts the room's backend turn and bind the
        handle it reveals.

        The call outlives a cancelled owner, so a turn the backend already
        accepted is still stopped once its handle arrives.
        """
        call = asyncio.ensure_future(start)
        try:
            handle = await asyncio.shield(call)
        except asyncio.CancelledError:
            self._spawn_stop(room_id, self._stop_when_started(room_id, call))
            raise
        if handle is not None:
            self.bind(room_id, handle)
        return handle

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

    async def stop(self, room_id: str) -> None:
        """Stop the room's backend turn and wait until that stop settles."""
        self.cancel(room_id)
        await self.settled(room_id)

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
        self._spawn_stop(room_id, self._cancel(handle))

    async def _stop_when_started(
        self, room_id: str, call: asyncio.Future[H | None]
    ) -> None:
        try:
            handle = await call
        except Exception as error:  # noqa: BLE001 -- a start that failed left no turn to stop
            logger.debug(
                "%s turn start in room %s failed after its turn was stopped: %r",
                self._provider,
                room_id,
                error,
            )
            return
        if handle is not None:
            await self._cancel(handle)

    def _spawn_stop(self, room_id: str, stop: Awaitable[None]) -> None:
        task = spawn_owned(
            asyncio.wait_for(stop, BACKEND_CANCEL_TIMEOUT_SECONDS),
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
    """Clear approvals and terminate the handler's worker on an interrupt.

    A room's leave and an adapter's stop belong to its residency owner, which
    refuses a closing room's work itself.
    """

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    _turn_tasks: dict[str, TurnTask]

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
        """Refuse new input while the room's turn or backend stop still runs."""
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

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        await super().on_interrupt(room_id, mode)  # type: ignore[misc]
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
