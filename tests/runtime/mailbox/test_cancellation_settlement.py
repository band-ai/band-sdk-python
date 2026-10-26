"""A cancelled ask retains its running job until cancellation cleanup settles."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold, SettlingJob, checkpoint


class TestCancellationSettlement:
    async def test_cancelled_queued_ask_does_not_join_the_running_job(self) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        submitted = asyncio.Event()
        ran = asyncio.Event()

        async def queued_job() -> None:
            ran.set()

        async def submit() -> None:
            submitted.set()
            await mailbox.ask("room-1", queued_job)

        blocker = asyncio.create_task(mailbox.ask("room-1", hold.pause))
        waiting: asyncio.Task[None] | None = None
        try:
            await hold.entered.wait()
            waiting = asyncio.create_task(submit())
            await submitted.wait()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not blocker.done()
            hold.release.set()
            await blocker
            await mailbox.ask("room-1", hold.pause)
            assert not ran.is_set()
        finally:
            hold.release.set()
            tasks = [blocker, *([waiting] if waiting is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await mailbox.close("room-1")

    @pytest.mark.parametrize(
        "error",
        [None, asyncio.CancelledError(), RuntimeError("cleanup failed")],
        ids=["suppressed", "propagated", "replaced"],
    )
    async def test_cancelled_ask_waits_for_cleanup_and_preserves_cancellation(
        self, error: BaseException | None
    ) -> None:
        mailbox = RoomMailbox()
        job = SettlingJob(error)
        successor_submitted = asyncio.Event()

        async def successor_job() -> None:
            assert job.finished.is_set(), "successor overlapped cancellation cleanup"

        async def submit_successor() -> None:
            successor_submitted.set()
            await mailbox.ask("room-1", successor_job)

        waiting = asyncio.create_task(mailbox.ask("room-1", job.run))
        successor: asyncio.Task[None] | None = None
        try:
            await job.running.entered.wait()
            waiting.cancel()
            await job.settling.entered.wait()
            successor = asyncio.create_task(submit_successor())
            await successor_submitted.wait()
            await checkpoint()
            assert not waiting.done()
            waiting.cancel()
            await checkpoint()
            assert not waiting.done()
            assert not successor.done()
            job.settling.release.set()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            await successor
        finally:
            job.running.release.set()
            job.settling.release.set()
            tasks = [waiting, *([successor] if successor is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await mailbox.close("room-1")
