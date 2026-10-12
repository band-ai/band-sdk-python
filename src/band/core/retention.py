"""Adapter-owned residency; native handles and continuation stay with adapters."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field

from band.core.adapterconfig import BaseAdapterConfig
from band.core.exceptions import CleanupIncompleteError

logger = logging.getLogger(__name__)
UNKNOWN_ACTIVITY_REASON = "Provider has not established authoritative quiescence"
BUSY_ACTIVITY_REASON = "Provider refused idle release"


class RoomRetentionConfig(BaseAdapterConfig):
    """Automatic suspension is opt-in and requires a qualified provider.

    ``transition_timeout_s`` bounds how long a caller waits to learn the result
    of one owned acquisition or cleanup; an unfinished one stays owned.
    """

    idle_timeout_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    transition_timeout_s: float = Field(default=10.0, gt=0, allow_inf_nan=False)


def require_disabled_retention(
    retention: RoomRetentionConfig, *, reason: str
) -> RoomRetentionConfig:
    if retention.idle_timeout_s is not None:
        raise ValueError(f"{reason} Leave retention.idle_timeout_s unset.")
    return retention


class ProviderActivity(StrEnum):
    ACTIVE = "active"
    QUIESCENT = "quiescent"
    UNKNOWN = "unknown"


class Residency(StrEnum):
    RESIDENT = "resident"
    SUSPENDING = "suspending"
    SUSPENDED = "suspended"
    ACQUIRING = "acquiring"
    CLEANUP_PENDING = "cleanup_pending"
    DISPOSING = "disposing"
    CLOSED = "closed"


class ReleaseCause(StrEnum):
    """Why a physical release runs; later members supersede earlier ones."""

    IDLE = "idle"
    RECOVERY = "recovery"
    FINAL = "final"


class ReleaseOutcome(StrEnum):
    """A release callback's successful result; raising means incomplete cleanup.

    ``BUSY`` is valid only for ``IDLE``, and only when nothing was torn down.
    """

    RELEASED = "released"
    BUSY = "busy"


_PRECEDENCE = tuple(ReleaseCause)


class RetentionSnapshot(BaseAdapterConfig):
    state: Residency
    generation: int
    leases: int
    accepted: int
    provider_activity: ProviderActivity
    reason: str
    deadline: float | None
    pending_release: ReleaseCause | None
    pending_cleanup: bool
    error: str | None


class RetentionClock(Protocol):
    def now(self) -> float: ...

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None: ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None:
        timeout = None if deadline is None else max(0, deadline - self.now())
        try:
            async with asyncio.timeout(timeout):
                await changed.wait()
        except TimeoutError:
            pass


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
        self._changed = asyncio.Event()
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
        except RuntimeError:
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
            raise RuntimeError(f"Room {self.room_id} is closing")

    async def _settle(self) -> None:
        """Finish earlier transitions and pending releases without a lease."""
        while True:
            self._check_admission()
            if self._transition is not None and not self._transition.done():
                await self._join(self._transition)
            elif self._cause is None:
                return
            elif self._leases:
                await self._changed.wait()
            else:
                self._start_release(self._cause, automatic=False)

    async def _ensure_resident(self) -> None:
        while True:
            self._check_admission()
            if self._transition is not None and not self._transition.done():
                await self._join(self._transition)
                continue
            match self._state:
                case Residency.RESIDENT:
                    return
                case Residency.SUSPENDED if self._cause is None:
                    await self._join(self._start_acquire(), initiator=True)
                case _:
                    raise RuntimeError(
                        f"Room {self.room_id} cleanup pending: {self._cause} release"
                    )

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
        if self._cause is None or _PRECEDENCE.index(cause) > _PRECEDENCE.index(
            self._cause
        ):
            self._cause = cause

    def _spawn(self, operation: Coroutine[object, object, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(operation)
        task.add_done_callback(self._observe_failure)
        return task

    def _observe_failure(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning(
                "Room %s residency transition failed: %s", self.room_id, error
            )
        self._start_requested_recovery()

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

    def _start_release(self, cause: ReleaseCause, *, automatic: bool) -> None:
        if cause == ReleaseCause.RECOVERY:
            self._recover_when_unleased = False
        self._request(cause)
        previous = self._state
        self._state = Residency.SUSPENDING
        self._deadline = None
        self._transition = self._spawn(
            self._run_release(cause, previous, automatic=automatic)
        )

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
            outcome = await self._release(cause)
            if outcome == ReleaseOutcome.BUSY and cause == ReleaseCause.IDLE:
                self._activity = ProviderActivity.ACTIVE
                self._reason = BUSY_ACTIVITY_REASON
                self._refuse_idle_release(intact=True)
                return
            if outcome != ReleaseOutcome.RELEASED:
                raise RuntimeError(f"{outcome!r} is not a valid {cause} release result")
        except BaseException as error:
            self._record_failure(error)
            raise
        else:
            self._state = Residency.SUSPENDED
            self._resume = self._resume or cause == ReleaseCause.IDLE
            self._error = None
            if self._cause != ReleaseCause.FINAL:
                self._cause = None
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

    def _record_failure(self, error: BaseException) -> None:
        self._state = Residency.CLEANUP_PENDING
        self._error = f"{type(error).__name__}: {error}"

    def _schedule(self, *, reset: bool = False) -> None:
        timeout = self._owner.config.idle_timeout_s
        idle_candidate = (
            self._state == Residency.RESIDENT and self._cause is None
        ) or (
            self._state == Residency.CLEANUP_PENDING
            and self._cause == ReleaseCause.IDLE
        )
        eligible = (
            timeout is not None
            and idle_candidate
            and not self._closing
            and not self._owner.stopping
            and not self._leases
            and not self._accepted
            and self._activity == ProviderActivity.QUIESCENT
        )
        if not eligible:
            self._deadline = None
        elif reset or self._deadline is None:
            assert timeout is not None
            self._deadline = self._owner.clock.now() + timeout
        self._changed.set()
        self._changed = asyncio.Event()
        self._owner.changed.set()

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
            self._start_release(ReleaseCause.FINAL, automatic=False)
            assert self._transition is not None
            await self._transition
        self._state = Residency.DISPOSING
        try:
            await self._dispose()
        except BaseException as error:
            self._state = Residency.SUSPENDED
            self._error = f"{type(error).__name__}: {error}"
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
        while self._accepted:
            await self._changed.wait()


def task_failure(task: asyncio.Task[Any]) -> BaseException | None:
    """A finished owned cleanup's failure; cancellation is incomplete cleanup."""
    if task.cancelled():
        return CleanupIncompleteError("Cleanup was cancelled")
    return task.exception()


async def report_owned(task: asyncio.Task[Any], timeout: float) -> None:
    """Report an owned cleanup's result within ``timeout`` without cancelling it."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        raise CleanupIncompleteError(f"Cleanup still running after {timeout}s")
    if (error := task_failure(task)) is not None:
        raise error


class RoomRetention:
    """One deadline scheduler per adapter; each room owns its awaited I/O."""

    def __init__(
        self, config: RoomRetentionConfig, *, clock: RetentionClock | None = None
    ) -> None:
        self.config = config
        self.clock = clock or MonotonicClock()
        self.changed = asyncio.Event()
        self.stopping = False
        self._rooms: dict[str, RetainedRoom] = {}
        self._scheduler: asyncio.Task[None] | None = None
        self._shutdown: asyncio.Task[None] | None = None

    def register(
        self,
        room_id: str,
        *,
        acquire: Callable[[int, bool], Awaitable[None]],
        release: Callable[[ReleaseCause], Awaitable[ReleaseOutcome]],
        dispose: Callable[[], Awaitable[None]],
    ) -> RetainedRoom:
        if self.stopping:
            raise RuntimeError("Room retention is stopping")
        if room_id in self._rooms:
            raise ValueError(f"Room {room_id} already has a residency owner")
        room = RetainedRoom(self, room_id, acquire, release, dispose)
        self._rooms[room_id] = room
        if self.config.idle_timeout_s is not None and self._scheduler is None:
            self._scheduler = asyncio.create_task(self._run())
        self.changed.set()
        return room

    def _forget(self, room: RetainedRoom) -> None:
        if self._rooms.get(room.room_id) is room:
            del self._rooms[room.room_id]
        self.changed.set()

    async def _run(self) -> None:
        while not self.stopping:
            self.changed.clear()
            deadline: float | None = None
            for room in self._rooms.values():
                due = room._deadline
                if due is None:
                    continue
                if due <= self.clock.now():
                    room._start_idle_release()
                elif deadline is None or due < deadline:
                    deadline = due
            await self.clock.wait(self.changed, deadline)

    async def stop(self) -> None:
        """Dispose every room under one reporting deadline; retain failures.

        Work enrolled in a room cannot await this shutdown; it is told cleanup
        is pending, is not cancelled by it, and is joined once it unwinds.
        """
        requested_from_inside = [
            room._spare_requester() for room in tuple(self._rooms.values())
        ]
        self.stopping = True
        self.changed.set()
        if self._shutdown is None or (
            self._shutdown.done()
            and (self._shutdown.cancelled() or self._shutdown.exception())
        ):
            self._shutdown = asyncio.create_task(self._run_stop())
            self._shutdown.add_done_callback(self._observe_shutdown)
        if any(requested_from_inside):
            raise CleanupIncompleteError(
                "Room retention shutdown waits for the calling work to finish"
            )
        await report_owned(self._shutdown, self.config.transition_timeout_s)

    @staticmethod
    def _observe_shutdown(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning("Room retention shutdown failed: %s", error)

    async def _run_stop(self) -> None:
        pending: list[asyncio.Task[None]] = []
        if self._scheduler is not None:
            pending.append(self._scheduler)
        pending.extend(
            disposal
            for room in tuple(self._rooms.values())
            if (disposal := room._begin_disposal()) is not None
        )
        if pending:
            await asyncio.wait(pending)
        failures = [error for task in pending if (error := task_failure(task))]
        if failures:
            raise BaseExceptionGroup("Room cleanup remains incomplete", failures)
