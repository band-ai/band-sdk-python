"""One serialized worker per room for provider occupancy.

Band delivery already has ``ExecutionContext._process_loop``. This mailbox is
the matching seam for *provider* work (subprocess RPC callbacks, model
select, cleanup) that must not become fake platform events. Callers enqueue;
each worker starts with an empty context so its jobs cannot inherit the first
submitter's task context.

Mailbox jobs cannot wait on more work from the same mailbox, preventing
cross-room wait cycles. Use ``cast`` for follow-up work that does not wait.
Occupancy ("this prompt is active") is not the worker ("one mutation now"): a
prompt waiter lives outside the mailbox so host-tool and UI jobs can run.
Cancelling ``ask`` cancels the in-flight job so a hung start cannot pin the
worker.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar

from band.core.exceptions import BandError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NestedMailboxAsk(BandError):
    """A mailbox job tried a nested operation that may wait on its mailbox."""


class MailboxClosing(BandError):
    """The room mailbox is draining and cannot accept more jobs."""


@dataclass
class MailboxJob:
    mailbox_identity: object
    run: Callable[[], Awaitable[Any]]
    result: asyncio.Future[Any] | None
    running: asyncio.Task[Any] | None = None


@dataclass
class MailboxRoom:
    queue: asyncio.Queue[MailboxJob | None] = field(default_factory=asyncio.Queue)
    worker: asyncio.Task[None] | None = None
    current: MailboxJob | None = None
    closing: bool = False


_current_job: ContextVar[MailboxJob | None] = ContextVar(
    "room_mailbox_current_job", default=None
)


class RoomMailbox:
    """Serialize FIFO jobs per room and reject nested operations that can wait."""

    def __init__(self) -> None:
        self._identity = object()
        self._rooms: dict[str, MailboxRoom] = {}

    def occupying(self, room_id: str) -> bool:
        """True on the worker or the job it is currently running."""
        room = self._rooms.get(room_id)
        if room is None:
            return False
        me = asyncio.current_task()
        if room.worker is me:
            return True
        current = room.current
        return current is not None and current.running is me

    def cast(self, room_id: str, run: Callable[[], Awaitable[None]]) -> None:
        """Enqueue ``run`` and return immediately, or raise while closing."""
        self._submit(room_id, run, result=None)

    def spawn(
        self, room_id: str, run: Callable[[], Awaitable[None]]
    ) -> asyncio.Future[None]:
        """Enqueue ``run`` and return a future, or raise when it cannot run safely.

        Raises from a job in this mailbox because the future could be awaited.
        Use ``cast`` for follow-up work that does not wait.
        """
        self._reject_nested_wait(room_id)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        job = self._submit(room_id, run, result=future)
        future.add_done_callback(partial(self._abandon_cancelled_spawn, job))
        return future

    async def ask(self, room_id: str, run: Callable[[], Awaitable[T]]) -> T:
        """Enqueue ``run`` and wait for its result.

        Raises ``NestedMailboxAsk`` from a job in this mailbox to prevent
        cross-room wait cycles, and ``MailboxClosing`` while this room's worker
        is draining. Cancelling the waiter cancels the in-flight job.
        """
        self._reject_nested_wait(room_id)
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        job = self._submit(room_id, run, result=future)
        try:
            return await future
        except asyncio.CancelledError:
            self._abandon(job)
            raise

    async def close(self, room_id: str) -> None:
        """Drain queued jobs and stop the worker; later submissions start anew.

        Raises ``NestedMailboxAsk`` from a job in this mailbox.
        """
        self._reject_nested_wait(room_id)
        room = self._rooms.get(room_id)
        if room is None:
            return
        if not room.closing:
            room.closing = True
            await room.queue.put(None)
        worker = room.worker
        if worker is not None:
            await asyncio.shield(worker)

    def _submit(
        self,
        room_id: str,
        run: Callable[[], Awaitable[Any]],
        result: asyncio.Future[Any] | None,
    ) -> MailboxJob:
        room = self._rooms.get(room_id)
        if room is not None and room.closing:
            raise MailboxClosing(f"Room {room_id} mailbox is closing")
        if room is None:
            room = MailboxRoom()
            self._rooms[room_id] = room
        self._start_worker(room_id, room)
        job = MailboxJob(self._identity, run, result)
        room.queue.put_nowait(job)
        return job

    def _reject_nested_wait(self, room_id: str) -> None:
        job = _current_job.get()
        is_this_mailbox_job = job is not None and job.mailbox_identity is self._identity
        if is_this_mailbox_job or self.occupying(room_id):
            raise NestedMailboxAsk(
                "Mailbox jobs cannot start nested operations that may wait"
            )

    def _start_worker(self, room_id: str, room: MailboxRoom) -> None:
        if room.worker is None or room.worker.done():
            room.worker = asyncio.create_task(
                self._run(room_id, room),
                name=f"room-mailbox:{room_id}",
                context=Context(),
            )

    @staticmethod
    def _abandon(job: MailboxJob) -> None:
        if job.running is not None and not job.running.done():
            job.running.cancel()
        elif job.result is not None and not job.result.done():
            job.result.cancel()

    @staticmethod
    def _abandon_cancelled_spawn(job: MailboxJob, future: asyncio.Future[Any]) -> None:
        if future.cancelled():
            RoomMailbox._abandon(job)

    async def _run(self, room_id: str, room: MailboxRoom) -> None:
        try:
            while True:
                job = await room.queue.get()
                if job is None:
                    return
                try:
                    await self._execute(room, job)
                finally:
                    room.queue.task_done()
                if room.queue.empty():
                    return
        finally:
            if room.worker is asyncio.current_task():
                room.worker = None
            if room.queue.empty():
                self._rooms.pop(room_id, None)
            elif room.worker is None:
                self._start_worker(room_id, room)

    async def _execute(self, room: MailboxRoom, job: MailboxJob) -> None:
        if job.result is not None and job.result.cancelled():
            return

        async def run_job() -> Any:
            # An eager task can enter the job before create_task returns.
            job.running = asyncio.current_task()
            token = _current_job.set(job)
            try:
                return await job.run()
            finally:
                _current_job.reset(token)

        room.current = job
        job.running = asyncio.create_task(run_job())
        try:
            value = await job.running
        except BaseException as error:
            if job.result is not None and not job.result.done():
                job.result.set_exception(error)
            elif job.result is None:
                logger.error("Room mailbox cast failed with %s", type(error).__name__)
            worker = asyncio.current_task()
            if (
                isinstance(error, asyncio.CancelledError)
                and worker is not None
                and worker.cancelling()
            ):
                raise
            return
        finally:
            if room.current is job:
                room.current = None
            job.running = None
        if job.result is not None and not job.result.done():
            job.result.set_result(value)
