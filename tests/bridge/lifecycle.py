"""Generated admission, cancellation and draining sequences for room forwarding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum, auto

from bridge_core.dispatch import RoomForwards
from hypothesis import strategies as st


class Step(StrEnum):
    ACCEPT_A = auto()
    ACCEPT_B = auto()
    ACCEPT_GLOBAL = auto()
    CANCEL_A = auto()
    CANCEL_B = auto()
    RELEASE_A = auto()
    RELEASE_B = auto()
    DRAIN = auto()


sequences = st.lists(st.sampled_from(Step), min_size=1, max_size=30)


@dataclass
class Job:
    room_id: str | None
    admitted: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    task: asyncio.Task[None] | None = None


class ForwardingLifecycle:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.forwards = RoomForwards(capacity)
        self.jobs: list[Job] = []
        self.running: set[str | None] = set()

    async def take(self, step: Step) -> None:
        match step:
            case Step.ACCEPT_A:
                await self.accept("a")
            case Step.ACCEPT_B:
                await self.accept("b")
            case Step.ACCEPT_GLOBAL:
                await self.accept(None)
            case Step.CANCEL_A:
                await self.cancel("a")
            case Step.CANCEL_B:
                await self.cancel("b")
            case Step.RELEASE_A:
                await self.release("a")
            case Step.RELEASE_B:
                await self.release("b")
            case Step.DRAIN:
                await self.drain()
        assert self.forwards.active_rooms() == {
            job.room_id
            for job in self.jobs
            if job.task is not None and not job.task.done()
        }

    async def accept(self, room_id: str | None) -> None:
        job = Job(room_id)
        self.jobs.append(job)
        job.task = asyncio.create_task(self.run(job))
        await job.admitted.wait()

    async def run(self, job: Job) -> None:
        with self.forwards.accepted(job.room_id) as room:
            job.admitted.set()
            async with self.forwards.ready(room):
                assert job.room_id not in self.running
                assert len(self.running) < self.capacity
                self.running.add(job.room_id)
                job.running = True
                try:
                    await job.release.wait()
                finally:
                    job.running = False
                    self.running.remove(job.room_id)

    async def cancel(self, room_id: str) -> None:
        pending = [
            job.task
            for job in self.jobs
            if job.room_id == room_id and job.task is not None and not job.task.done()
        ]
        assert self.forwards.cancel(room_id) == len(pending)
        await self.join(pending)

    async def release(self, room_id: str) -> None:
        """Settle the room's running job while other rooms keep holding or waiting."""
        for job in self.jobs:
            if job.room_id == room_id and job.running and job.task is not None:
                job.release.set()
                await self.join([job.task])
                return

    async def drain(self) -> None:
        for job in self.jobs:
            job.release.set()
        await self.join([job.task for job in self.jobs if job.task is not None])
        assert self.running == set()
        assert self.forwards.active_rooms() == set()

    async def close(self) -> None:
        for job in self.jobs:
            if job.task is not None:
                job.task.cancel()
        await self.join([job.task for job in self.jobs if job.task is not None])

    @staticmethod
    async def join(tasks: list[asyncio.Task[None]]) -> None:
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                raise result


async def play(steps: list[Step], capacity: int) -> None:
    lifecycle = ForwardingLifecycle(capacity)
    try:
        for step in steps:
            await lifecycle.take(step)
        await lifecycle.drain()
    finally:
        await lifecycle.close()
