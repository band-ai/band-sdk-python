"""Generated operation sequences for mailbox queue and worker lifecycles."""

from __future__ import annotations

import asyncio
from enum import StrEnum, auto
from functools import partial

from hypothesis import strategies as st

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold, SettlingJob, checkpoint

ROOM_ID = "lifecycle-room"
SEQUENCE_GUARD_S = 5.0


class Step(StrEnum):
    CAST = auto()
    SPAWN = auto()
    ASK = auto()
    CANCEL_QUEUED_ASK = auto()
    CANCEL_RUNNING_ASK = auto()
    CLOSE = auto()
    REOPEN = auto()


sequences = st.lists(st.sampled_from(Step), min_size=1, max_size=12)


class MailboxLifecycle:
    """Compare completed jobs with a model while applying public operations."""

    def __init__(self) -> None:
        self.mailbox = RoomMailbox()
        self.expected: list[str] = []
        self.completed: list[str] = []
        self.spawned: list[asyncio.Future[None]] = []
        self.next_id = 0

    async def take(self, step: Step) -> None:
        match step:
            case Step.CAST:
                self.submit_cast()
            case Step.SPAWN:
                self.submit_spawn()
            case Step.ASK:
                await self.ask_next()
            case Step.CANCEL_QUEUED_ASK:
                await self.cancel_queued_ask()
            case Step.CANCEL_RUNNING_ASK:
                await self.cancel_running_ask()
            case Step.CLOSE:
                await self.close_and_check()
            case Step.REOPEN:
                await self.close_and_check()
                await self.ask_next()

    async def finish(self) -> None:
        await self.close_and_check()

    def submit_cast(self) -> None:
        label = self.new_label()
        self.expected.append(label)
        self.mailbox.cast(ROOM_ID, partial(self.record_completion, label))

    def submit_spawn(self) -> None:
        label = self.new_label()
        self.expected.append(label)
        future = self.mailbox.spawn(ROOM_ID, partial(self.record_completion, label))
        self.spawned.append(future)

    async def ask_next(self) -> None:
        label = self.new_label()
        self.expected.append(label)
        result = await self.mailbox.ask(ROOM_ID, partial(self.answer, label))
        assert result == label
        assert self.completed == self.expected

    async def cancel_queued_ask(self) -> None:
        hold = JobHold()
        hold.hold()
        blocker_label = self.new_label()
        self.expected.append(blocker_label)

        async def blocked_job() -> None:
            await hold.pause()
            self.completed.append(blocker_label)

        blocker = asyncio.create_task(self.mailbox.ask(ROOM_ID, blocked_job))
        await hold.entered.wait()

        cancelled_job_ran = asyncio.Event()
        submitted = asyncio.Event()

        async def cancelled_job() -> None:
            cancelled_job_ran.set()

        async def submit_cancelled_job() -> None:
            submitted.set()
            await self.mailbox.ask(ROOM_ID, cancelled_job)

        cancelled = asyncio.create_task(submit_cancelled_job())
        await submitted.wait()
        cancelled.cancel()
        try:
            await cancelled
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("queued ask should be cancelled")

        hold.release.set()
        await blocker
        barrier_label = self.new_label()
        self.expected.append(barrier_label)
        result = await self.mailbox.ask(ROOM_ID, partial(self.answer, barrier_label))
        assert result == barrier_label
        assert not cancelled_job_ran.is_set()

    async def close_and_check(self) -> None:
        await self.mailbox.close(ROOM_ID)
        for future in self.spawned:
            await future
        assert self.completed == self.expected

    async def cancel_running_ask(self) -> None:
        job = SettlingJob(None)
        waiting = asyncio.create_task(self.mailbox.ask(ROOM_ID, job.run))
        try:
            await job.running.entered.wait()
            waiting.cancel()
            await job.settling.entered.wait()
            waiting.cancel()
            await checkpoint()
            assert not waiting.done()
            assert not job.finished.is_set()
            job.settling.release.set()
            try:
                await waiting
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("running ask should preserve cancellation")
            assert job.finished.is_set()
        finally:
            job.running.release.set()
            job.settling.release.set()
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    async def record_completion(self, label: str) -> None:
        self.completed.append(label)

    async def answer(self, label: str) -> str:
        self.completed.append(label)
        return label

    def new_label(self) -> str:
        label = f"job-{self.next_id}"
        self.next_id += 1
        return label


async def play(steps: list[Step]) -> None:
    lifecycle = MailboxLifecycle()
    for step in steps:
        await lifecycle.take(step)
    await lifecycle.finish()
