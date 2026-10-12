"""Room residency ownership with controlled time and explicit I/O barriers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    ReleaseOutcome,
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


@pytest_asyncio.fixture(loop_scope="function")
async def host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost()
    try:
        yield host
    finally:
        await host.close()


@pytest_asyncio.fixture(loop_scope="function")
async def impatient_host() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost(transition_timeout=0.05)
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


async def test_abandoned_startup_is_recovered_before_reuse(
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
    await resident.suspended.get()
    await resident.turn()
    assert resident.releases == [ReleaseCause.RECOVERY]
    assert resident.resume_requests == [False, False]
    assert resident.identity == "native-1"
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


async def test_partial_acquisition_keeps_handle_until_recovery_succeeds(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    resident.fail_acquire = resident.fail_close = True
    with pytest.raises(RuntimeError, match="resume rejected"):
        await resident.turn()
    await resident.suspended.get()
    with pytest.raises(RuntimeError, match="close refused"):
        await resident.turn()
    assert resident.live
    assert len(resident.resume_requests) == 1
    assert resident.room.snapshot.pending_release is ReleaseCause.RECOVERY
    resident.fail_close = resident.fail_acquire = False
    await resident.turn()
    assert resident.live
    assert len(resident.resume_requests) == 2
    assert set(resident.releases) == {ReleaseCause.RECOVERY}


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
    assert resident.releases == [ReleaseCause.FINAL]
    assert resident.identity is None
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
    assert failed.identity is not None and other.identity is None
    assert failed.room.snapshot.pending_cleanup
    with pytest.raises(RuntimeError):
        await failed.turn()
    failed.fail_dispose = False
    await host.owner.stop()
    assert failed.identity is None
    assert failed.releases == [ReleaseCause.FINAL]


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
    assert resident.identity is None


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


async def test_owned_queued_work_revokes_idle_until_it_actually_finishes(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    queued = Barrier()

    async def detached_turn() -> None:
        await queued.wait()
        await resident.turn()

    task = asyncio.create_task(detached_turn())
    resident.room.own(task)
    assert resident.room.snapshot.accepted == 1
    assert resident.room.snapshot.deadline is None
    await host.clock.advance(100)
    assert resident.live and not resident.releases
    queued.released.set()
    await task
    assert resident.room.snapshot.accepted == 0
    await host.clock.advance(10)
    await resident.suspended.get()
    assert resident.releases == [ReleaseCause.IDLE]


async def test_queued_work_cancelled_before_use_releases_ownership(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    task = asyncio.create_task(resident.turn())
    resident.room.own(task)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert resident.room.snapshot.accepted == 0
    await host.clock.advance(10)
    await resident.suspended.get()
    await resident.room.dispose()
    assert resident.room.snapshot.state is Residency.CLOSED


async def test_final_cleanup_cancels_and_joins_owned_work(host: ResidenceHost) -> None:
    resident = host.room()
    await resident.turn()
    parked = Barrier()

    async def parked_decision() -> None:
        async with resident.room.use():
            await parked.wait()

    task = asyncio.create_task(parked_decision())
    resident.room.own(task)
    await parked.reached.wait()
    await resident.room.dispose()
    assert task.cancelled()
    assert resident.releases == [ReleaseCause.FINAL]
    with pytest.raises(RuntimeError, match="closing"):
        resident.room.own(asyncio.create_task(resident.turn()))


async def test_cancellation_resistant_work_keeps_disposal_pending(
    impatient_host: ResidenceHost,
) -> None:
    resident = impatient_host.room()
    await resident.turn()
    finish = asyncio.Event()

    async def stubborn() -> None:
        async with resident.room.use():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await finish.wait()

    task = asyncio.create_task(stubborn())
    resident.room.own(task)
    await asyncio.sleep(0)
    with pytest.raises(CleanupIncompleteError):
        await resident.room.dispose()
    assert resident.live and not resident.releases
    assert resident.room.snapshot.pending_cleanup
    finish.set()
    await task
    await resident.room.dispose()
    assert resident.releases == [ReleaseCause.FINAL]


async def test_enrolled_work_cannot_await_its_own_disposal(
    host: ResidenceHost,
) -> None:
    resident = host.room()

    async def leave_from_inside() -> None:
        async with resident.room.use():
            with pytest.raises(CleanupIncompleteError):
                await resident.room.dispose()
            assert resident.live

    await leave_from_inside()
    await resident.room.dispose()
    assert resident.room.snapshot.state is Residency.CLOSED
    assert resident.releases == [ReleaseCause.FINAL]


async def test_successor_waits_for_release_before_lease_and_preparation(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.closing = Barrier()
    await host.clock.advance(10)
    await resident.closing.reached.wait()
    prepared: list[int] = []

    async def prepare() -> None:
        prepared.append(resident.room.snapshot.leases)

    async def successor() -> None:
        async with resident.room.use(prepare):
            assert resident.live

    task = asyncio.create_task(successor())
    await asyncio.sleep(0)
    assert resident.room.snapshot.accepted == 1
    assert resident.room.snapshot.leases == 0
    assert prepared == []
    resident.closing.released.set()
    await task
    assert prepared == [1]
    assert resident.resume_requests == [False, True]


async def test_failed_idle_close_retries_before_waiting_successor_leases(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_close = True
    await host.clock.advance(10)
    await resident.suspended.get()
    resident.fail_close = False
    await resident.turn()
    assert resident.releases == [ReleaseCause.IDLE, ReleaseCause.IDLE]
    assert resident.resume_requests == [False, True]


async def test_failed_acquisition_is_recovered_never_idled(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    resident.startup_activity = ProviderActivity.ACTIVE
    resident.fail_acquire = True
    resident.closing = Barrier()
    with pytest.raises(RuntimeError, match="resume rejected"):
        await resident.turn()
    await resident.closing.reached.wait()
    assert resident.releases == [ReleaseCause.RECOVERY]
    resident.closing.released.set()
    await resident.suspended.get()
    await host.clock.advance(100)
    assert resident.releases == [ReleaseCause.RECOVERY]


async def test_identity_created_before_startup_failure_survives_recovery(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    resident.fail_acquire = True
    with pytest.raises(RuntimeError, match="resume rejected"):
        await resident.turn()
    resident.fail_acquire = False
    await resident.turn()
    assert resident.identity == "native-1"
    assert resident.room.snapshot.generation == 2


async def test_busy_idle_refusal_keeps_the_intact_residency(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    await resident.turn()
    resident.busy = True
    await host.clock.advance(10)
    await resident.suspended.get()
    snapshot = resident.room.snapshot
    assert snapshot.state is Residency.RESIDENT
    assert snapshot.provider_activity is ProviderActivity.ACTIVE
    assert not snapshot.pending_cleanup
    await resident.turn()
    resident.busy = False
    resident.observe(ProviderActivity.QUIESCENT)
    await host.clock.advance(10)
    await resident.suspended.get()
    assert not resident.live
    assert resident.resume_requests == [False]


async def test_recovery_waits_for_the_reporting_operation_to_unwind(
    host: ResidenceHost,
) -> None:
    resident = host.room()
    successors: list[asyncio.Task[None]] = []

    async def crashing_turn() -> None:
        async with resident.room.use():
            generation = resident.room.snapshot.generation
            assert resident.room.recover(generation=generation, reason="exited")
            assert not resident.room.observe(
                ProviderActivity.QUIESCENT, generation=generation
            )
            successors.append(asyncio.create_task(resident.turn()))
            await asyncio.sleep(0)
            snapshot = resident.room.snapshot
            assert snapshot.state is Residency.CLEANUP_PENDING
            assert (snapshot.leases, snapshot.accepted) == (1, 2)

    await crashing_turn()
    await successors[0]
    assert resident.releases == [ReleaseCause.RECOVERY]
    assert resident.resume_requests == [False, False]
    assert resident.room.snapshot.generation == 2


async def test_stale_holder_events_cannot_mutate_a_rejoined_room(
    host: ResidenceHost,
) -> None:
    old = host.room()
    await old.turn()
    await old.room.dispose()
    new = host.room()
    await new.turn()
    new.observe(ProviderActivity.ACTIVE)
    assert old.room.snapshot.generation == new.room.snapshot.generation
    assert not old.room.observe(ProviderActivity.QUIESCENT, generation=1)
    assert not old.room.recover(generation=1, reason="stale")
    assert new.room.snapshot.provider_activity is ProviderActivity.ACTIVE
    assert not new.room.snapshot.pending_cleanup


async def test_acquisition_report_timeout_keeps_it_owned_for_recovery(
    impatient_host: ResidenceHost,
) -> None:
    resident = impatient_host.room()
    resident.opening = Barrier()
    with pytest.raises(TimeoutError):
        await resident.turn()
    assert resident.room.snapshot.state is Residency.ACQUIRING
    resident.opening.released.set()
    await resident.suspended.get()
    await resident.turn()
    assert resident.releases == [ReleaseCause.RECOVERY]
    assert resident.resume_requests == [False, False]


async def test_disposal_report_timeout_is_joined_without_a_second_close(
    impatient_host: ResidenceHost,
) -> None:
    resident = impatient_host.room()
    await resident.turn()
    resident.closing = Barrier()
    with pytest.raises(CleanupIncompleteError):
        await resident.room.dispose()
    assert resident.room.snapshot.state is Residency.SUSPENDING
    assert resident.room.snapshot.pending_release is ReleaseCause.FINAL
    resident.closing.released.set()
    await resident.room.dispose()
    assert resident.releases == [ReleaseCause.FINAL]


@pytest.mark.parametrize("failure", ["release", "dispose"])
async def test_failed_final_cleanup_retries_the_same_handle(
    host: ResidenceHost, failure: str
) -> None:
    resident = host.room()
    await resident.turn()
    resident.fail_close = failure == "release"
    resident.fail_dispose = failure == "dispose"
    with pytest.raises(RuntimeError, match="refused"):
        await resident.room.dispose()
    assert resident.room.snapshot.pending_cleanup
    assert resident.identity == "native-1"
    resident.fail_close = resident.fail_dispose = False
    await resident.room.dispose()
    expected = 2 if failure == "release" else 1
    assert resident.releases == [ReleaseCause.FINAL] * expected
    assert resident.identity is None
