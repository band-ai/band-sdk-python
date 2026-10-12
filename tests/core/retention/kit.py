"""Room residency hosts with controlled time and explicit I/O barriers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    ReleaseOutcome,
    RetainedRoom,
    RoomRetention,
    RoomRetentionConfig,
)


class ManualClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.changed: asyncio.Event | None = None
        self.waits: asyncio.Queue[None] = asyncio.Queue()

    def now(self) -> float:
        return self.time

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None:
        self.changed = changed
        self.waits.put_nowait(None)
        if deadline is None or deadline > self.time:
            await changed.wait()

    async def advance(self, seconds: float) -> None:
        while not self.waits.empty():
            self.waits.get_nowait()
        self.time += seconds
        if self.changed is not None:
            self.changed.set()
        await self.waits.get()


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

    async def acquire(self, generation: int, resume: bool) -> None:
        assert not self.live, "replacement started before the previous handle closed"
        self.live = True
        self.identity = self.identity or f"native-{generation}"
        self.resume_requests.append(resume)
        self.room.observe(self.startup_activity, generation=generation)
        if self.opening is not None:
            await self.opening.wait()
        if self.fail_acquire:
            raise RuntimeError("resume rejected")

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


class ResidenceHost:
    def __init__(
        self, timeout: float | None = 10, transition_timeout: float = 10.0
    ) -> None:
        self.clock = ManualClock()
        self.owner = RoomRetention(
            RoomRetentionConfig(
                idle_timeout_s=timeout, transition_timeout_s=transition_timeout
            ),
            clock=self.clock,
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
