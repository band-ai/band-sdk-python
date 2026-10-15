"""The adapter-wide owner: room registry, idle scheduler and shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.core.retention.cleanup import (
    await_owned,
    rejoin_owned,
    report_owned,
    task_failure,
)
from band.core.retention.clock import Cancellable, MonotonicClock, RetentionClock
from band.core.retention.config import RoomRetentionConfig
from band.core.retention.room import RetainedRoom
from band.core.retention.states import ReleaseCause, ReleaseOutcome


class RoomRetention:
    """One idle timer per adapter; each room owns its awaited I/O.

    The timer is armed for the earliest room deadline and re-armed on every
    room change, so nothing polls and nothing runs between deadlines.
    """

    def __init__(
        self,
        config: RoomRetentionConfig,
        *,
        clock: RetentionClock | None = None,
        settle_grace_s: float = 0.0,
    ) -> None:
        """``settle_grace_s`` is how long final disposal lets accepted work
        finish on its own, admission already closed, before cancelling it."""
        self.config = config
        self.clock = clock or MonotonicClock()
        self.settle_grace_s = settle_grace_s
        self.stopping = False
        self._rooms: dict[str, RetainedRoom] = {}
        self._timer: Cancellable | None = None
        self._shutdown: asyncio.Task[None] | None = None

    def register(
        self,
        room_id: str,
        *,
        acquire: Callable[[int, bool], Awaitable[None]],
        release: Callable[[ReleaseCause], Awaitable[ReleaseOutcome]],
        dispose: Callable[[], Awaitable[None]],
        before_cancel_acquire: Callable[[asyncio.Task[None], str], bool] | None = None,
    ) -> RetainedRoom:
        self.require_admission()
        if room_id in self._rooms:
            raise ValueError(f"Room {room_id} already has a residency owner")
        room = RetainedRoom(
            self,
            room_id,
            acquire,
            release,
            dispose,
            before_cancel_acquire=before_cancel_acquire,
        )
        self._rooms[room_id] = room
        self.reschedule()
        return room

    def require_admission(self) -> None:
        """Refuse new work before it claims provider resources."""
        if self.stopping:
            raise RoomClosingError("Room retention is stopping")

    def _forget(self, room: RetainedRoom) -> None:
        if self._rooms.get(room.room_id) is room:
            del self._rooms[room.room_id]
        self.reschedule()

    def reschedule(self) -> None:
        """Arm the timer for the earliest idle deadline, if any."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        deadlines = [room._deadline for room in self._rooms.values()]
        upcoming = min((due for due in deadlines if due is not None), default=None)
        if upcoming is not None:
            self._timer = self.clock.call_at(
                upcoming, partial(self._release_due_rooms, upcoming)
            )

    def _release_due_rooms(self, due: float) -> None:
        """Start every idle release due by ``due``; each clears its deadline.

        Rooms are compared with the armed deadline rather than ``now`` because
        the loop may fire a timer up to its clock resolution early.
        """
        self._timer = None
        for room in tuple(self._rooms.values()):
            if room._deadline is not None and room._deadline <= due:
                room._start_idle_release()
        self.reschedule()

    async def stop(self) -> None:
        """Dispose every room under one reporting deadline; retain failures.

        Work enrolled in a room cannot await this shutdown; it is told cleanup
        is pending, is not cancelled by it, and is joined once it unwinds.
        """
        requested_from_inside = [
            room._spare_requester() for room in tuple(self._rooms.values())
        ]
        self.stopping = True
        self.reschedule()
        self._shutdown = shutdown = rejoin_owned(
            self._shutdown, self._run_stop, label="Room retention shutdown"
        )
        if any(requested_from_inside):
            raise CleanupIncompleteError(
                "Room retention shutdown waits for the calling work to finish"
            )
        await report_owned(shutdown, self.config.transition_timeout_s)

    def renewed(self) -> RoomRetention:
        """The owner to serve with after a restart.

        A stop is terminal for its owner, so a stopped one is replaced by a
        fresh owner with the same settings, and only once its stop confirmed
        every room closed.

        Raises:
            CleanupIncompleteError: The stop is unfinished or failed.
        """
        if not self.stopping:
            return self
        if self._rooms:
            raise CleanupIncompleteError(
                "Room retention cannot renew while rooms from its stop remain"
            )
        return RoomRetention(
            self.config, clock=self.clock, settle_grace_s=self.settle_grace_s
        )

    async def _run_stop(self) -> None:
        pending = [
            disposal
            for room in tuple(self._rooms.values())
            if (disposal := room._begin_disposal()) is not None
        ]
        if pending:
            await await_owned(pending, None)
        failures = [error for task in pending if (error := task_failure(task))]
        if failures:
            raise BaseExceptionGroup("Room cleanup remains incomplete", failures)
