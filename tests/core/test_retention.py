"""Room residency ownership with controlled time and explicit I/O barriers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio

from band.core.retention import (
    ProviderActivity,
    Residency,
    RetainedRoom,
    RoomRetention,
    RoomRetentionConfig,
)

pytestmark = pytest.mark.timeout(10)


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
    resume_requests: list[bool] = field(default_factory=list)
    suspended: asyncio.Queue[None] = field(default_factory=asyncio.Queue)
    opening: Barrier | None = None
    closing: Barrier | None = None
    leaving: Barrier | None = None
    fail_close: bool = False
    fail_acquire: bool = False
    fail_dispose: bool = False

    async def acquire(self, generation: int, resume: bool) -> None:
        assert not self.live, "replacement started before the previous handle closed"
        self.live = True
        self.resume_requests.append(resume)
        if self.opening is not None:
            await self.opening.wait()
        if self.fail_acquire:
            raise RuntimeError("resume rejected")
        self.room.observe(ProviderActivity.QUIESCENT, generation=generation)

    async def suspend(self) -> None:
        if self.closing is not None:
            await self.closing.wait()
        self.suspended.put_nowait(None)
        if self.fail_close:
            raise RuntimeError("close refused")
        self.live = False

    async def dispose(self) -> None:
        if self.leaving is not None:
            await self.leaving.wait()
        if self.fail_dispose:
            raise RuntimeError("dispose refused")
        self.live = False

    async def turn(self) -> None:
        async with self.room.use():
            assert self.live

    def observe(self, activity: ProviderActivity, reason: str = "") -> bool:
        return self.room.observe(
            activity, generation=self.room.snapshot.generation, reason=reason
        )


class ResidenceHost:
    def __init__(self, timeout: float | None = 10) -> None:
        self.clock = ManualClock()
        self.owner = RoomRetention(
            RoomRetentionConfig(idle_timeout_s=timeout), clock=self.clock
        )
        self.residents: list[Resident] = []

    def room(self, name: str = "room") -> Resident:
        resident = Resident()
        resident.room = self.owner.register(
            name,
            acquire=resident.acquire,
            suspend=resident.suspend,
            dispose=resident.dispose,
        )
        self.residents.append(resident)
        return resident

    async def close(self) -> None:
        for resident in self.residents:
            resident.fail_close = resident.fail_acquire = resident.fail_dispose = False
            for barrier in (resident.opening, resident.closing, resident.leaving):
                if barrier is not None:
                    barrier.released.set()
        await self.owner.stop()


@pytest_asyncio.fixture(loop_scope="function")
async def host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost()
    try:
        yield host
    finally:
        await host.close()


async def test_idle_rooms_release_independently_of_other_room_traffic(
    host: ResidenceHost,
) -> None:
    idle = host.room("idle")
    busy = host.room("busy")
    await idle.turn()
    await busy.turn()
    await host.clock.advance(6)
    await busy.turn()
    await host.clock.advance(4)
    await idle.suspended.get()
    assert not idle.live
    assert busy.live
    await host.clock.advance(6)
    await busy.suspended.get()
    assert not busy.live


@pytest.mark.parametrize(
    "activity", [ProviderActivity.ACTIVE, ProviderActivity.UNKNOWN]
)
async def test_provider_completion_rearms_without_another_message(
    host: ResidenceHost, activity: ProviderActivity
) -> None:
    resident = host.room()
    await resident.turn()
    resident.observe(activity, "background work remains unaccounted for")
    await host.clock.advance(100)
    assert resident.live
    assert resident.room.snapshot.deadline is None
    resident.observe(ProviderActivity.QUIESCENT)
    await host.clock.advance(10)
    await resident.suspended.get()
    assert not resident.live


async def test_actual_work_lease_pins_a_quiescent_provider(host: ResidenceHost) -> None:
    resident = host.room()
    async with resident.room.use():
        await host.clock.advance(100)
        assert resident.live
        assert resident.room.snapshot.deadline is None
    await host.clock.advance(10)
    await resident.suspended.get()
    assert not resident.live


async def test_repeated_quiescence_does_not_postpone_release(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    await host.clock.advance(6)
    resident.observe(ProviderActivity.QUIESCENT)
    await host.clock.advance(4)
    await resident.suspended.get()
    assert not resident.live


async def test_native_activity_restarts_the_whole_idle_interval(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    await host.clock.advance(6)
    resident.observe(ProviderActivity.ACTIVE)
    resident.observe(ProviderActivity.QUIESCENT)
    await host.clock.advance(4)
    assert resident.live
    await host.clock.advance(6)
    await resident.suspended.get()
    assert not resident.live


async def test_slow_close_does_not_block_another_room(host: ResidenceHost) -> None:
    slow = host.room("slow")
    fast = host.room("fast")
    await slow.turn()
    await fast.turn()
    slow.closing = Barrier()
    await host.clock.advance(10)
    await slow.closing.reached.wait()
    await fast.suspended.get()
    await fast.turn()
    assert slow.live and fast.live
    assert len(fast.resume_requests) == 2


async def test_failed_close_retries_on_deadline_without_a_message(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_close = True
    await host.clock.advance(10)
    await resident.suspended.get()
    assert resident.live
    assert resident.room.snapshot.pending_cleanup
    await host.clock.advance(9)
    assert resident.suspended.empty()
    resident.fail_close = False
    await host.clock.advance(1)
    await resident.suspended.get()
    assert not resident.live
    assert not resident.room.snapshot.pending_cleanup


async def test_failed_close_cannot_create_a_replacement(host: ResidenceHost) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_close = True
    await host.clock.advance(10)
    await resident.suspended.get()
    with pytest.raises(RuntimeError, match="close refused"):
        await resident.turn()
    assert len(resident.resume_requests) == 1
    assert resident.live


@pytest.mark.parametrize(
    "activity", [ProviderActivity.ACTIVE, ProviderActivity.UNKNOWN]
)
async def test_new_native_work_prevents_retrying_a_failed_idle_close(
    host: ResidenceHost, activity: ProviderActivity
) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_close = True
    await host.clock.advance(10)
    await resident.suspended.get()
    resident.fail_close = False
    resident.observe(activity)
    with pytest.raises(RuntimeError):
        await resident.turn()
    await host.clock.advance(100)
    assert resident.live
    assert resident.suspended.empty()
    resident.observe(ProviderActivity.QUIESCENT)
    await host.clock.advance(10)
    await resident.suspended.get()
    assert not resident.live


async def test_cancelled_wake_waiter_does_not_cancel_close_or_duplicate_acquisition(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.closing = Barrier()
    await host.clock.advance(10)
    await resident.closing.reached.wait()
    admitted = asyncio.Event()

    async def wake_room() -> None:
        admitted.set()
        await resident.turn()

    wake = asyncio.create_task(wake_room())
    await admitted.wait()
    wake.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wake
    resident.closing.released.set()
    await asyncio.gather(resident.turn(), resident.turn())
    assert len(resident.resume_requests) == 2
    assert resident.resume_requests[-1] is True
    assert resident.live


async def test_native_activity_queued_before_retry_close_prevents_release(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_close = True
    await host.clock.advance(10)
    await resident.suspended.get()
    resident.fail_close = False
    wake = asyncio.create_task(resident.turn())
    asyncio.get_running_loop().call_soon(resident.observe, ProviderActivity.ACTIVE)
    with pytest.raises(RuntimeError, match="cleanup pending"):
        await wake
    assert resident.live
    assert len(resident.resume_requests) == 1
    assert resident.suspended.empty()


async def test_cancelled_acquire_waiter_keeps_owned_startup(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    resident.opening = Barrier()
    wake = asyncio.create_task(resident.turn())
    await resident.opening.reached.wait()
    wake.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wake
    resident.opening.released.set()
    await resident.turn()
    assert len(resident.resume_requests) == 1
    assert resident.live


async def test_failed_resume_remains_strict_and_cleans_up_before_retry(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    await host.clock.advance(10)
    await resident.suspended.get()
    resident.fail_acquire = True
    with pytest.raises(RuntimeError, match="resume rejected"):
        await resident.turn()
    resident.fail_acquire = False
    await resident.turn()
    assert resident.resume_requests == [False, True, True]
    assert resident.live


async def test_partial_acquisition_keeps_handle_until_cleanup_succeeds(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    resident.fail_acquire = True
    with pytest.raises(RuntimeError, match="resume rejected"):
        await resident.turn()
    resident.fail_close = True
    with pytest.raises(RuntimeError, match="close refused"):
        await resident.turn()
    assert resident.live
    assert len(resident.resume_requests) == 1
    assert resident.room.snapshot.pending_cleanup
    resident.fail_close = resident.fail_acquire = False
    await resident.turn()
    assert resident.live
    assert len(resident.resume_requests) == 2


async def test_old_process_events_cannot_unpin_replacement(host: ResidenceHost) -> None:
    resident = host.room()
    await resident.turn()
    old_generation = resident.room.snapshot.generation
    await host.clock.advance(10)
    await resident.suspended.get()
    await resident.turn()
    resident.observe(ProviderActivity.ACTIVE)
    assert not resident.room.observe(
        ProviderActivity.QUIESCENT, generation=old_generation
    )
    await host.clock.advance(10)
    assert resident.live


async def test_leave_wins_against_wake_during_close(host: ResidenceHost) -> None:
    resident = host.room()
    await resident.turn()
    resident.closing = Barrier()
    await host.clock.advance(10)
    await resident.closing.reached.wait()
    leaving = asyncio.create_task(resident.room.dispose())
    wake = asyncio.create_task(resident.turn())
    resident.closing.released.set()
    await leaving
    with pytest.raises(RuntimeError):
        await wake
    assert len(resident.resume_requests) == 1
    assert resident.room.snapshot.state is Residency.CLOSED
    assert not resident.live


async def test_cancelled_disposal_waiter_leaves_retryable_owned_cleanup(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.leaving = Barrier()
    leaving = asyncio.create_task(resident.room.dispose())
    await resident.leaving.reached.wait()
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    resident.leaving.released.set()
    await resident.room.dispose()
    assert not resident.live
    assert resident.room.snapshot.state is Residency.CLOSED


async def test_shutdown_attempts_every_room_and_retries_failed_ownership(
    host: ResidenceHost,
) -> None:
    failed = host.room("failed")
    other = host.room("other")
    await failed.turn()
    await other.turn()
    failed.fail_dispose = True
    with pytest.raises(ExceptionGroup):
        await host.owner.stop()
    assert failed.live and not other.live
    assert failed.room.snapshot.pending_cleanup
    with pytest.raises(RuntimeError):
        await failed.turn()
    failed.fail_dispose = False
    await host.owner.stop()
    assert not failed.live


async def test_cancelled_shutdown_keeps_cleanup_owned_and_blocks_wake(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.leaving = Barrier()
    stopping = asyncio.create_task(host.owner.stop())
    await resident.leaving.reached.wait()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    with pytest.raises(RuntimeError):
        await resident.turn()
    resident.leaving.released.set()
    await host.owner.stop()
    assert not resident.live


async def test_shutdown_preempts_queued_provider_acquisition(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    wake = asyncio.create_task(resident.turn())
    stopping = asyncio.create_task(host.owner.stop())
    with pytest.raises(RuntimeError, match="closing"):
        await wake
    await stopping
    assert resident.resume_requests == []
    assert not resident.live


async def test_failed_disposal_retains_room_registration(host: ResidenceHost) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_dispose = True
    with pytest.raises(RuntimeError, match="dispose refused"):
        await resident.room.dispose()
    with pytest.raises((RuntimeError, ValueError)):
        host.room()
    resident.fail_dispose = False
    await resident.room.dispose()
    replacement = host.room()
    await replacement.turn()
    assert replacement.live and not resident.live
    assert not resident.observe(ProviderActivity.QUIESCENT)


async def test_disabled_retention_still_disposes_owned_resources() -> None:
    host = ResidenceHost(timeout=None)
    try:
        resident = host.room()
        await resident.turn()
        assert resident.room.snapshot.deadline is None
        await host.owner.stop()
        assert not resident.live
    finally:
        await host.close()
