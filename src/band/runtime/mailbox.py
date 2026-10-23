"""One serialized worker per room for provider occupancy.

Band delivery already has ``ExecutionContext._process_loop``. This mailbox is
the matching seam for *provider* work (subprocess RPC callbacks, model
select, cleanup) that must not become fake platform events. Callers enqueue;
they do not copy task context onto child tasks.

``ask`` from the room's own worker raises. Occupancy ("this prompt is active")
is not the worker ("one mutation now"): a prompt waiter lives outside the
mailbox so host-tool and UI jobs can run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from band.core.exceptions import BandError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NestedMailboxAsk(BandError):
    """The room worker asked itself and would wait forever."""


@dataclass
class _Job:
    run: Callable[[], Awaitable[Any]]
    result: asyncio.Future[Any] | None


@dataclass
class _Room:
    queue: asyncio.Queue[_Job | None] = field(default_factory=asyncio.Queue)
    worker: asyncio.Task[None] | None = None


class RoomMailbox:
    """FIFO worker per room: ``cast`` does not wait, ``ask`` does."""

    def __init__(self) -> None:
        self._rooms: dict[str, _Room] = {}

    def occupying(self, room_id: str) -> bool:
        """True only while this task *is* the room's worker."""
        room = self._rooms.get(room_id)
        return room is not None and room.worker is asyncio.current_task()

    def cast(self, room_id: str, run: Callable[[], Awaitable[None]]) -> None:
        """Enqueue ``run`` and return immediately."""
        self._submit(room_id, run, result=None)

    def spawn(
        self, room_id: str, run: Callable[[], Awaitable[None]]
    ) -> asyncio.Future[None]:
        """Enqueue ``run`` and return a future that settles with it."""
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._submit(room_id, run, result=future)
        return future

    async def ask(self, room_id: str, run: Callable[[], Awaitable[T]]) -> T:
        """Enqueue ``run`` and wait for its result.

        Raises ``NestedMailboxAsk`` when the caller is this room's worker.
        """
        if self.occupying(room_id):
            raise NestedMailboxAsk(
                f"Room {room_id} mailbox asked itself and would wait forever"
            )
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._submit(room_id, run, result=future)
        return await future

    async def close(self, room_id: str) -> None:
        """Drain queued jobs, then stop the worker."""
        if self.occupying(room_id):
            raise NestedMailboxAsk(
                f"Room {room_id} mailbox cannot close from its own worker"
            )
        room = self._rooms.get(room_id)
        if room is None:
            return
        await room.queue.put(None)
        worker = room.worker
        if worker is not None:
            await worker

    def _submit(
        self,
        room_id: str,
        run: Callable[[], Awaitable[Any]],
        result: asyncio.Future[Any] | None,
    ) -> None:
        room = self._rooms.get(room_id)
        if room is None:
            room = _Room()
            self._rooms[room_id] = room
        if room.worker is None or room.worker.done():
            room.worker = asyncio.create_task(
                self._run(room_id, room), name=f"room-mailbox:{room_id}"
            )
        room.queue.put_nowait(_Job(run, result))

    async def _run(self, room_id: str, room: _Room) -> None:
        try:
            while True:
                job = await room.queue.get()
                if job is None:
                    return
                try:
                    await self._execute(room_id, job)
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
                room.worker = asyncio.create_task(
                    self._run(room_id, room), name=f"room-mailbox:{room_id}"
                )

    async def _execute(self, room_id: str, job: _Job) -> None:
        if job.result is not None and job.result.cancelled():
            return
        try:
            value = await job.run()
        except BaseException as error:
            if job.result is not None and not job.result.done():
                job.result.set_exception(error)
            elif job.result is None:
                logger.exception("Room mailbox %s cast failed", room_id)
            if isinstance(error, asyncio.CancelledError):
                raise
            return
        if job.result is not None and not job.result.done():
            job.result.set_result(value)
