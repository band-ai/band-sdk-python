"""One room's residency: admission, acquisition, release and disposal."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.core.retention.cleanup import report_owned, spawn_owned
from band.core.retention.states import (
    BUSY_ACTIVITY_REASON,
    UNKNOWN_ACTIVITY_REASON,
    ProviderActivity,
    ReleaseCause,
    ReleaseOutcome,
    Residency,
    RetentionSnapshot,
    stronger_cause,
)

if TYPE_CHECKING:
    from band.core.retention.owner import RoomRetention

logger = logging.getLogger(__name__)

# Circuit breaker. One step joins a transition, waits for leases, or runs one
# release. Settle allows an in-flight join, a lease wait, that release, a
# stronger cause recorded during it, a confirming pass, and one spare step.
# Residency allows a join, one acquire, and the pass that sees the room resident.
MAX_SETTLE_STEPS = 6
MAX_RESIDENCY_STEPS = 3


def _hold(signal: asyncio.Event, active: bool) -> None:
    """Keep a level signal set exactly while ``active``."""
    if active:
        signal.set()
    else:
        signal.clear()


def _failure_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


class RetainedRoom:
    """Serialize residency transitions without owning provider objects.

    Callbacks retain concrete handles until closure succeeds. ``acquire`` must
    strictly resume the retained native identity when its resume flag is true.
    ``release`` closes the physical handle for a ``ReleaseCause``; an ``IDLE``
    release must be a qualified, non-cancelling boundary, and every retry must
    finish partially acquired or closed handles. ``dispose`` runs only after a
    confirmed ``FINAL`` release and erases continuation and workspace.
    """

    def __init__(
        self,
        owner: RoomRetention,
        room_id: str,
        acquire: Callable[[int, bool], Awaitable[None]],
        release: Callable[[ReleaseCause], Awaitable[ReleaseOutcome]],
        dispose: Callable[[], Awaitable[None]],
    ) -> None:
        self._owner = owner
        self.room_id = room_id
        self._acquire = acquire
        self._release = release
        self._dispose = dispose
        self._state = Residency.SUSPENDED
        self._generation = 0
        self._leases = 0
        self._owned: set[asyncio.Task[Any]] = set()
        # Owned tasks that requested this room's cleanup and unwind themselves.
        self._unwinding: set[asyncio.Task[Any]] = set()
        self._holders: Counter[asyncio.Task[Any] | None] = Counter()
        self._activity = ProviderActivity.UNKNOWN
        self._reason = UNKNOWN_ACTIVITY_REASON
        self._deadline: float | None = None
        self._error: str | None = None
        self._cause: ReleaseCause | None = None
        self._recover_when_unleased = False
        self._resume = False
        self._closing = False
        self._work_cancelled = False
        # Level signals re-derived on every change: a settling operation waits
        # for its leases to drain or admission to close, final disposal for
        # every accepted operation to finish.
        self._settleable = asyncio.Event()
        self._settleable.set()
        self._drained = asyncio.Event()
        self._drained.set()
        self._transition: asyncio.Task[None] | None = None
        self._disposal: asyncio.Task[None] | None = None

    @property
    def snapshot(self) -> RetentionSnapshot:
        pending = None if self._state == Residency.CLOSED else self._cause
        return RetentionSnapshot(
            state=self._state,
            generation=self._generation,
            leases=self._leases,
            accepted=self._accepted,
            provider_activity=self._activity,
            reason=self._reason,
            deadline=self._deadline,
            pending_release=pending,
            pending_cleanup=self._state == Residency.CLEANUP_PENDING
            or pending in (ReleaseCause.RECOVERY, ReleaseCause.FINAL),
            error=self._error,
        )

    @property
    def _accepted(self) -> int:
        return len(self._owned | set(self._holders))

    def observe(
        self,
        activity: ProviderActivity,
        *,
        generation: int,
        reason: str = "",
    ) -> bool:
        """Accept authoritative provider observations from this residency only."""
        if not self._has_authority(generation):
            return False
        changed = self._activity != activity
        self._activity = activity
        self._reason = reason or (
            UNKNOWN_ACTIVITY_REASON if activity == ProviderActivity.UNKNOWN else ""
        )
        self._schedule(reset=changed)
        return True

    def _has_authority(self, generation: int) -> bool:
        return (
            generation == self._generation
            and not self._closing
            and self._cause in (None, ReleaseCause.IDLE)
            and self._state not in (Residency.SUSPENDED, Residency.CLOSED)
        )

    def own(self, task: asyncio.Task[Any]) -> None:
        """Enroll accepted detached work before its first await.

        Enrolled work blocks idle release and is settled by final disposal until
        the task actually finishes. A refused enrollment cancels the task.
        """
        try:
            self._check_admission()
        except RoomClosingError:
            task.cancel()
            raise
        self._owned.add(task)
        task.add_done_callback(self._disown)
        self._schedule()

    def _disown(self, task: asyncio.Task[Any]) -> None:
        self._owned.discard(task)
        self._unwinding.discard(task)
        self._schedule()

    def recover(self, *, generation: int, reason: str) -> bool:
        """Retire a crashed or stale residency through this owner.

        Events from that residency lose authority at once. The concrete handle
        stays with the provider until a ``RECOVERY`` release confirms closure,
        which starts once no operation holds an active I/O lease.
        """
        if not self._has_authority(generation):
            return False
        self._request(ReleaseCause.RECOVERY)
        self._recover_when_unleased = True
        self._activity = ProviderActivity.UNKNOWN
        self._reason = reason
        self._error = reason
        if self._state == Residency.RESIDENT:
            self._state = Residency.CLEANUP_PENDING
        self._schedule()
        self._start_requested_recovery()
        return True

    @asynccontextmanager
    async def use(
        self, prepare: Callable[[], Awaitable[None]] | None = None
    ) -> AsyncIterator[None]:
        """Pass one accepted operation through the ready-I/O order.

        Earlier transitions settle before this operation leases active I/O;
        ``prepare`` then binds the operation before acquisition or strict
        restore. The lease lasts through actual work, detached decisions
        included. An operation that abandons its acquisition leaves it for
        recovery rather than for a successor to adopt.
        """
        self._check_admission()
        holder = asyncio.current_task()
        self._holders[holder] += 1
        self._schedule()
        try:
            await self._settle()
            self._check_admission()
            self._leases += 1
            try:
                if prepare is not None:
                    await prepare()
                await self._ensure_resident()
                yield
            finally:
                self._leases -= 1
        finally:
            self._holders -= Counter({holder: 1})
            self._schedule()
            self._start_requested_recovery()

    def _check_admission(self) -> None:
        if self._closing or self._owner.stopping:
            raise RoomClosingError(f"Room {self.room_id} is closing")

    async def _settle(self) -> None:
        """Finish earlier transitions and pending releases without a lease."""
        for _ in range(MAX_SETTLE_STEPS):
            self._check_admission()
            match self._in_flight(), self._cause, self._leases:
                case asyncio.Task() as transition, _, _:
                    await self._join(transition)
                case None, None, _:
                    return
                case None, ReleaseCause() as cause, 0:
                    await self._join(self._start_release(cause, automatic=False))
                case _:
                    await self._settleable.wait()
        raise self._stalled(MAX_SETTLE_STEPS)

    async def _ensure_resident(self) -> None:
        for _ in range(MAX_RESIDENCY_STEPS):
            self._check_admission()
            match self._in_flight(), self._state, self._cause:
                case asyncio.Task() as transition, _, _:
                    await self._join(transition)
                case None, Residency.RESIDENT, _:
                    return
                case None, Residency.SUSPENDED, None:
                    await self._join(self._start_acquire(), initiator=True)
                case _:
                    raise RuntimeError(
                        f"Room {self.room_id} cleanup pending: {self._cause} release"
                    )
        raise self._stalled(MAX_RESIDENCY_STEPS)

    def _in_flight(self) -> asyncio.Task[None] | None:
        transition = self._transition
        return transition if transition is not None and not transition.done() else None

    def _stalled(self, steps: int) -> RuntimeError:
        """The circuit breaker: transitions kept changing under one operation."""
        logger.error("Room %s did not settle within %d steps", self.room_id, steps)
        return RuntimeError(f"Room {self.room_id} did not settle within {steps} steps")

    async def _join(
        self, transition: asyncio.Task[None], *, initiator: bool = False
    ) -> None:
        """Wait a bounded time for a transition without cancelling it."""
        timeout = self._owner.config.transition_timeout_s
        try:
            done, _ = await asyncio.wait({transition}, timeout=timeout)
        except asyncio.CancelledError:
            if initiator:
                self._abandon()
            raise
        if not done:
            if initiator:
                self._abandon()
            raise TimeoutError(
                f"Room {self.room_id} transition still running after {timeout}s"
            )
        if transition.cancelled():
            self._check_admission()
            raise RuntimeError(f"Room {self.room_id} transition was cancelled")
        if (error := transition.exception()) is not None:
            raise error

    def _abandon(self) -> None:
        """An acquisition its initiator stopped waiting for needs recovery."""
        self._request(ReleaseCause.RECOVERY)
        self._recover_when_unleased = True
        if self._state == Residency.RESIDENT:
            self._state = Residency.CLEANUP_PENDING

    def _request(self, cause: ReleaseCause) -> None:
        self._cause = stronger_cause(self._cause, cause)

    def _spawn(self, operation: Coroutine[object, object, None]) -> asyncio.Task[None]:
        task = spawn_owned(operation, label=f"Room {self.room_id} residency transition")
        task.add_done_callback(lambda _: self._start_requested_recovery())
        return task

    def _start_acquire(self) -> asyncio.Task[None]:
        self._generation += 1
        self._state = Residency.ACQUIRING
        self._activity = ProviderActivity.UNKNOWN
        self._reason = UNKNOWN_ACTIVITY_REASON
        self._transition = self._spawn(self._run_acquire())
        return self._transition

    async def _run_acquire(self) -> None:
        if self._closing or self._owner.stopping:
            self._state = Residency.SUSPENDED
            self._schedule()
            self._check_admission()
        try:
            await self._acquire(self._generation, self._resume)
        except BaseException as error:
            self._abandon()
            self._record_failure(error)
            raise
        else:
            if self._cause is None:
                self._state = Residency.RESIDENT
                self._resume = False
                self._error = None
            else:
                self._state = Residency.CLEANUP_PENDING
        finally:
            self._schedule(reset=True)

    def _start_requested_recovery(self) -> None:
        if (
            self._recover_when_unleased
            and self._cause == ReleaseCause.RECOVERY
            and self._state == Residency.CLEANUP_PENDING
            and not self._leases
            and not self._closing
            and (self._transition is None or self._transition.done())
        ):
            self._start_release(ReleaseCause.RECOVERY, automatic=False)

    def _start_release(
        self, cause: ReleaseCause, *, automatic: bool
    ) -> asyncio.Task[None]:
        if cause == ReleaseCause.RECOVERY:
            self._recover_when_unleased = False
        self._request(cause)
        previous = self._state
        self._state = Residency.SUSPENDING
        self._deadline = None
        self._transition = self._spawn(
            self._run_release(cause, previous, automatic=automatic)
        )
        return self._transition

    async def _run_release(
        self, cause: ReleaseCause, previous: Residency, *, automatic: bool
    ) -> None:
        if cause == ReleaseCause.IDLE and not self._idle_release_allowed(automatic):
            self._refuse_idle_release(intact=previous == Residency.RESIDENT)
            if not automatic:
                self._check_admission()
                raise RuntimeError(
                    f"Room {self.room_id} cleanup pending: provider activity "
                    f"is {self._activity}; {self._reason or 'waiting for quiescence'}"
                )
            return
        try:
            match cause, await self._release(cause):
                case ReleaseCause.IDLE, ReleaseOutcome.BUSY:
                    self._activity = ProviderActivity.ACTIVE
                    self._reason = BUSY_ACTIVITY_REASON
                    self._refuse_idle_release(intact=True)
                    return
                case _, ReleaseOutcome.RELEASED:
                    pass
                case _, outcome:
                    raise RuntimeError(
                        f"{outcome!r} is not a valid {cause} release result"
                    )
        except BaseException as error:
            self._record_failure(error)
            raise
        else:
            self._commit_release(cause)
        finally:
            self._schedule(reset=True)

    def _idle_release_allowed(self, automatic: bool) -> bool:
        return (
            self._cause == ReleaseCause.IDLE
            and not self._closing
            and not self._owner.stopping
            and not self._leases
            and not (automatic and self._accepted)
            and self._activity == ProviderActivity.QUIESCENT
        )

    def _refuse_idle_release(self, *, intact: bool) -> None:
        """Keep an intact handle resident; a stronger pending cause still applies."""
        if self._cause == ReleaseCause.IDLE and intact:
            self._cause = None
            self._state = Residency.RESIDENT
        else:
            self._state = Residency.CLEANUP_PENDING
        self._schedule()

    def _commit_release(self, cause: ReleaseCause) -> None:
        """A confirmed release. A stronger cause recorded during it stays pending."""
        self._error = None
        pending = self._cause
        if pending is not None and stronger_cause(cause, pending) != cause:
            self._state = Residency.CLEANUP_PENDING
            self._resume = False
            return
        self._state = Residency.SUSPENDED
        self._resume = self._resume or cause == ReleaseCause.IDLE
        if self._cause != ReleaseCause.FINAL:
            self._cause = None

    def _record_failure(self, error: BaseException) -> None:
        self._state = Residency.CLEANUP_PENDING
        self._error = _failure_text(error)

    def _schedule(self, *, reset: bool = False) -> None:
        """Re-arm the idle deadline and wake everything waiting on a change."""
        self._rearm_deadline(reset=reset)
        self._notify()

    def _rearm_deadline(self, *, reset: bool) -> None:
        timeout = self._owner.config.idle_timeout_s
        if timeout is None or not self._idle_eligible:
            self._deadline = None
        elif reset or self._deadline is None:
            self._deadline = self._owner.clock.now() + timeout

    @property
    def _idle_eligible(self) -> bool:
        return (
            self._idle_candidate
            and not self._closing
            and not self._owner.stopping
            and not self._leases
            and not self._accepted
            and self._activity == ProviderActivity.QUIESCENT
        )

    @property
    def _idle_candidate(self) -> bool:
        """A residency an idle release may retire, or retry retiring."""
        match self._state, self._cause:
            case (Residency.RESIDENT, None) | (
                Residency.CLEANUP_PENDING,
                ReleaseCause.IDLE,
            ):
                return True
            case _:
                return False

    def _notify(self) -> None:
        settleable = not self._leases or self._closing or self._owner.stopping
        _hold(self._settleable, settleable)
        _hold(self._drained, not self._accepted)
        self._owner.reschedule()

    def _start_idle_release(self) -> None:
        self._start_release(ReleaseCause.IDLE, automatic=True)

    def enrolls(self, task: asyncio.Task[Any] | None) -> bool:
        """Whether ``task`` is accepted work this room's disposal would await."""
        return task in self._owned or task in self._holders

    def _spare_requester(self) -> bool:
        """Exempt enrolled work requesting cleanup from cancellation; it is
        told cleanup is pending and still joined once it unwinds."""
        current = asyncio.current_task()
        if not self.enrolls(current):
            return False
        if current is not None and current in self._owned:
            self._unwinding.add(current)
        return True

    async def dispose(self) -> None:
        """Close admission now; settle accepted work before physical disposal.

        The wait is bounded by ``transition_timeout_s`` and an unfinished
        disposal stays owned for a retry to join. Work enrolled here cannot
        await its own disposal: it is told cleanup is pending, is not
        cancelled by it, and is joined once it unwinds.
        """
        requested_from_inside = self._spare_requester()
        disposal = self._begin_disposal()
        if disposal is None:
            return
        if requested_from_inside:
            raise CleanupIncompleteError(
                f"Room {self.room_id} disposal waits for the calling work to finish"
            )
        await report_owned(disposal, self._owner.config.transition_timeout_s)

    def _begin_disposal(self) -> asyncio.Task[None] | None:
        self._closing = True
        self._request(ReleaseCause.FINAL)
        self._schedule()
        if self._state == Residency.CLOSED:
            return None
        if self._disposal is None or self._disposal.done():
            self._disposal = self._spawn(self._run_dispose())
        return self._disposal

    async def _run_dispose(self) -> None:
        await self._settle_accepted()
        if self._transition is not None:
            await asyncio.wait({self._transition})
        if self._state != Residency.SUSPENDED:
            await self._start_release(ReleaseCause.FINAL, automatic=False)
        self._state = Residency.DISPOSING
        try:
            await self._dispose()
        except BaseException as error:
            self._state = Residency.SUSPENDED
            self._error = _failure_text(error)
            raise
        else:
            self._state = Residency.CLOSED
            self._error = None
            self._owner._forget(self)
        finally:
            self._schedule()

    async def _settle_accepted(self) -> None:
        """Cancel accepted tasks and interrupted startup once, then join all."""
        if not self._work_cancelled:
            self._work_cancelled = True
            for task in self._owned - self._unwinding:
                task.cancel()
            if self._state == Residency.ACQUIRING and self._transition is not None:
                self._transition.cancel()
        await self._drained.wait()
