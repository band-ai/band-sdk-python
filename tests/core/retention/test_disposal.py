"""Final disposal and owner shutdown keep unfinished cleanup owned."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import (
    ProviderActivity,
    ReleaseCause,
    Residency,
)
from tests.core.retention.kit import Barrier, ResidenceHost

pytestmark = pytest.mark.timeout(10)


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
