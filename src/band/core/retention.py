"""Adapter-owned residency; native handles and continuation stay with adapters."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from band.core.adapterconfig import BaseAdapterConfig

logger = logging.getLogger(__name__)
UNKNOWN_ACTIVITY_REASON = "Provider has not established authoritative quiescence"


class RoomRetentionConfig(BaseAdapterConfig):
    """Automatic suspension is opt-in and requires a qualified provider."""

    idle_timeout_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)


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


class RetentionSnapshot(BaseAdapterConfig):
    state: Residency
    generation: int
    leases: int
    provider_activity: ProviderActivity
    reason: str
    deadline: float | None
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
    ``suspend`` must implement a qualified, non-cancelling release boundary;
    retries must safely finish partially acquired or closed handles as well.
    ``dispose`` may cancel work and also releases continuation and workspace.
    """

    def __init__(
        self,
        owner: RoomRetention,
        room_id: str,
        acquire: Callable[[int, bool], Awaitable[None]],
        suspend: Callable[[], Awaitable[None]],
        dispose: Callable[[], Awaitable[None]],
    ) -> None:
        self._owner = owner
        self.room_id = room_id
        self._acquire = acquire
        self._suspend = suspend
        self._dispose = dispose
        self._state = Residency.SUSPENDED
        self._generation = 0
        self._leases = 0
        self._activity = ProviderActivity.UNKNOWN
        self._reason = UNKNOWN_ACTIVITY_REASON
        self._deadline: float | None = None
        self._error: str | None = None
        self._resume = False
        self._failed_acquisition = False
        self._closing = False
        self._transition: asyncio.Task[None] | None = None
        self._disposal: asyncio.Task[None] | None = None

    @property
    def snapshot(self) -> RetentionSnapshot:
        return RetentionSnapshot(
            state=self._state,
            generation=self._generation,
            leases=self._leases,
            provider_activity=self._activity,
            reason=self._reason,
            deadline=self._deadline,
            pending_cleanup=self._state == Residency.CLEANUP_PENDING,
            error=self._error,
        )

    def observe(
        self,
        activity: ProviderActivity,
        *,
        generation: int,
        reason: str = "",
    ) -> bool:
        """Accept authoritative provider observations from this residency only."""
        if (
            generation != self._generation
            or self._closing
            or self._state in (Residency.SUSPENDED, Residency.CLOSED)
        ):
            return False
        changed = self._activity != activity
        self._activity = activity
        self._reason = reason or (
            UNKNOWN_ACTIVITY_REASON if activity == ProviderActivity.UNKNOWN else ""
        )
        self._schedule(reset=changed)
        return True

    @asynccontextmanager
    async def use(self) -> AsyncIterator[None]:
        """Keep the lease through actual work, including detached decisions."""
        self._check_admission()
        self._leases += 1
        self._schedule()
        try:
            await self._ensure_resident()
            yield
        finally:
            self._leases -= 1
            self._schedule()

    def _check_admission(self) -> None:
        if self._closing or self._owner.stopping:
            raise RuntimeError(f"Room {self.room_id} is closing")

    async def _ensure_resident(self) -> None:
        while True:
            self._check_admission()
            if self._transition is not None and not self._transition.done():
                await asyncio.shield(self._transition)
                continue
            match self._state:
                case Residency.RESIDENT:
                    return
                case Residency.CLEANUP_PENDING:
                    self._start_suspend(guarded=False)
                case Residency.SUSPENDED:
                    self._generation += 1
                    self._state = Residency.ACQUIRING
                    self._activity = ProviderActivity.UNKNOWN
                    self._reason = UNKNOWN_ACTIVITY_REASON
                    self._transition = self._owned(self._run_acquire())
                case _:
                    raise RuntimeError(f"Cannot acquire room in {self._state}")
            assert self._transition is not None
            await asyncio.shield(self._transition)

    def _owned(self, operation: Coroutine[object, object, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(operation)
        task.add_done_callback(self._observe_failure)
        return task

    def _observe_failure(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning(
                "Room %s residency transition failed: %s", self.room_id, error
            )

    async def _run_acquire(self) -> None:
        try:
            self._check_admission()
            await self._acquire(self._generation, self._resume)
        except BaseException as error:
            self._failed_acquisition = True
            self._record_failure(error)
            raise
        else:
            self._state = Residency.RESIDENT
            self._error = None
        finally:
            self._schedule(reset=True)

    def _start_suspend(self, *, guarded: bool) -> None:
        previous = self._state
        self._state = Residency.SUSPENDING
        self._deadline = None
        self._transition = self._owned(self._run_suspend(previous, guarded=guarded))

    async def _run_suspend(self, previous: Residency, *, guarded: bool) -> None:
        if (
            self._closing
            or self._owner.stopping
            or not self._provider_allows_close
            or (guarded and self._leases)
        ):
            self._state = previous
            self._schedule()
            if not guarded:
                self._check_admission()
                raise RuntimeError(
                    f"Room {self.room_id} cleanup pending: provider activity "
                    f"is {self._activity}; {self._reason or 'waiting for quiescence'}"
                )
            return
        if previous == Residency.RESIDENT:
            self._resume = True
        try:
            await self._suspend()
        except BaseException as error:
            self._record_failure(error)
            raise
        else:
            self._state = Residency.SUSPENDED
            self._failed_acquisition = False
            self._error = None
        finally:
            self._schedule(reset=True)

    def _record_failure(self, error: BaseException) -> None:
        self._state = Residency.CLEANUP_PENDING
        self._error = f"{type(error).__name__}: {error}"

    @property
    def _provider_allows_close(self) -> bool:
        # Failed acquisition never admitted work; partial setup still needs cleanup.
        return self._failed_acquisition or self._activity == ProviderActivity.QUIESCENT

    def _schedule(self, *, reset: bool = False) -> None:
        timeout = self._owner.config.idle_timeout_s
        eligible = (
            timeout is not None
            and not self._closing
            and not self._owner.stopping
            and not self._leases
            and self._provider_allows_close
            and self._state in (Residency.RESIDENT, Residency.CLEANUP_PENDING)
        )
        if not eligible:
            self._deadline = None
        elif reset or self._deadline is None:
            assert timeout is not None
            self._deadline = self._owner.clock.now() + timeout
        self._owner.changed.set()

    async def dispose(self) -> None:
        """Stop admission immediately; a cancelled caller does not cancel cleanup."""
        self._closing = True
        self._schedule()
        if self._state == Residency.CLOSED:
            return
        if self._disposal is None or self._disposal.done():
            self._disposal = self._owned(self._run_dispose())
        await asyncio.shield(self._disposal)

    async def _run_dispose(self) -> None:
        if self._transition is not None:
            await asyncio.gather(self._transition, return_exceptions=True)
        self._state = Residency.DISPOSING
        try:
            await self._dispose()
        except BaseException as error:
            self._record_failure(error)
            raise
        else:
            self._state = Residency.CLOSED
            self._error = None
            self._owner._forget(self)


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
        suspend: Callable[[], Awaitable[None]],
        dispose: Callable[[], Awaitable[None]],
    ) -> RetainedRoom:
        if self.stopping:
            raise RuntimeError("Room retention is stopping")
        if room_id in self._rooms:
            raise ValueError(f"Room {room_id} already has a residency owner")
        room = RetainedRoom(self, room_id, acquire, suspend, dispose)
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
                    room._start_suspend(guarded=True)
                elif deadline is None or due < deadline:
                    deadline = due
            await self.clock.wait(self.changed, deadline)

    async def stop(self) -> None:
        """Join owned transitions and retain failed room owners for a later retry."""
        self.stopping = True
        self.changed.set()
        if self._shutdown is None or self._shutdown.done():
            self._shutdown = asyncio.create_task(self._run_stop())
            self._shutdown.add_done_callback(self._observe_shutdown)
        await asyncio.shield(self._shutdown)

    @staticmethod
    def _observe_shutdown(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning("Room retention shutdown failed: %s", error)

    async def _run_stop(self) -> None:
        results = await asyncio.gather(
            *((self._scheduler,) if self._scheduler is not None else ()),
            *(room.dispose() for room in tuple(self._rooms.values())),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise BaseExceptionGroup("Room cleanup remains incomplete", failures)
