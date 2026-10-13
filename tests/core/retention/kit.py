"""Room residency hosts with controlled time and explicit I/O barriers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    ReleaseOutcome,
    RetainedRoom,
    RoomRetention,
    RoomRetentionConfig,
)


@dataclass
class ManualTimer:
    when: float
    callback: Callable[[], None]
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualClock:
    """Test time: ``advance`` fires the timers it passes, in deadline order."""

    def __init__(self) -> None:
        self.time = 0.0
        self.timers: list[ManualTimer] = []

    def now(self) -> float:
        return self.time

    def call_at(self, when: float, callback: Callable[[], None]) -> ManualTimer:
        timer = ManualTimer(when, callback)
        self.timers.append(timer)
        return timer

    async def advance(self, seconds: float) -> None:
        self.time += seconds
        due = sorted(
            (t for t in self.timers if not t.cancelled and t.when <= self.time),
            key=lambda t: t.when,
        )
        self.timers = [t for t in self.timers if t not in due and not t.cancelled]
        for timer in due:
            if not timer.cancelled:
                timer.callback()
        await asyncio.sleep(0)


@dataclass
class Barrier:
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> None:
        self.reached.set()
        await self.released.wait()


@dataclass
class Resident:
    room: RetainedRoom = field(init=False)
    live: bool = False
    identity: str | None = None
    resume_requests: list[bool] = field(default_factory=list)
    releases: list[ReleaseCause] = field(default_factory=list)
    suspended: asyncio.Queue[None] = field(default_factory=asyncio.Queue)
    opening: Barrier | None = None
    closing: Barrier | None = None
    leaving: Barrier | None = None
    startup_activity: ProviderActivity = ProviderActivity.QUIESCENT
    busy: bool = False
    fail_close: bool = False
    fail_acquire: bool = False
    fail_dispose: bool = False
    resists_cancel: bool = False
    # Set once a resistant start took its cancellation and parked to finish.
    resisted: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, generation: int, resume: bool) -> None:
        assert not self.live, "replacement started before the previous handle closed"
        self.live = True
        self.identity = self.identity or f"native-{generation}"
        self.resume_requests.append(resume)
        self.room.observe(self.startup_activity, generation=generation)
        if self.opening is not None:
            await self._open(self.opening)
        if self.fail_acquire:
            raise RuntimeError("resume rejected")

    async def _open(self, opening: Barrier) -> None:
        """Wait at the opening barrier; a resistant start finishes there anyway."""
        try:
            await opening.wait()
        except asyncio.CancelledError:
            if not self.resists_cancel:
                raise
            self.resisted.set()
            await opening.released.wait()

    async def release(self, cause: ReleaseCause) -> ReleaseOutcome:
        if cause is not ReleaseCause.FINAL:
            assert self.room.snapshot.leases == 0, "released under an active lease"
        self.releases.append(cause)
        if self.closing is not None:
            await self.closing.wait()
        self.suspended.put_nowait(None)
        if self.busy:
            return ReleaseOutcome.BUSY
        if self.fail_close:
            raise RuntimeError("close refused")
        self.live = False
        return ReleaseOutcome.RELEASED

    async def dispose(self) -> None:
        assert not self.live, "durable state disposed before confirmed closure"
        if self.leaving is not None:
            await self.leaving.wait()
        if self.fail_dispose:
            raise RuntimeError("dispose refused")
        self.identity = None

    async def turn(self) -> None:
        async with self.room.use():
            assert self.live

    def observe(self, activity: ProviderActivity, reason: str = "") -> bool:
        return self.room.observe(
            activity, generation=self.room.snapshot.generation, reason=reason
        )


#: The settle grace a graceful host's disposal gives accepted work.
SETTLE_GRACE_S = 5.0


class ResidenceHost:
    def __init__(
        self,
        timeout: float | None = 10,
        transition_timeout: float = 10.0,
        settle_grace: float = 0.0,
    ) -> None:
        self.clock = ManualClock()
        self.owner = RoomRetention(
            RoomRetentionConfig(
                idle_timeout_s=timeout, transition_timeout_s=transition_timeout
            ),
            clock=self.clock,
            settle_grace_s=settle_grace,
        )
        self.residents: list[Resident] = []

    def room(self, name: str = "room") -> Resident:
        resident = Resident()
        resident.room = self.owner.register(
            name,
            acquire=resident.acquire,
            release=resident.release,
            dispose=resident.dispose,
        )
        self.residents.append(resident)
        return resident

    async def close(self) -> None:
        for resident in self.residents:
            resident.fail_close = resident.fail_acquire = resident.fail_dispose = False
            resident.busy = False
            for barrier in (resident.opening, resident.closing, resident.leaving):
                if barrier is not None:
                    barrier.released.set()
        await self.owner.stop()


def armed(clock: ManualClock) -> list[float]:
    """Deadlines of the timers still waiting to fire."""
    return [timer.when for timer in clock.timers if not timer.cancelled]


#: The logger every retention module logs under.
RETENTION_LOGGER = "band.core.retention"


def retention_logs(records: list[logging.LogRecord]) -> list[tuple[int, str]]:
    """Each retention log line as (level, message)."""
    return [
        (record.levelno, record.getMessage())
        for record in records
        if record.name.startswith(RETENTION_LOGGER)
    ]
