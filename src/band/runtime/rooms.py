"""Each room's owned execution: admission, routing and retirement.

A room is a small state machine with at most one step in flight. A signal
(join, leave, stop, reconnect, an event for an unserved room) or the end of
that step advances it once; nothing polls. Waiters await the room's
``Convergence``, which settles when the room matches its intent and fails
when a step fails, leaving the execution owned for the next signal to retry.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import Any

from band.core.exceptions import CleanupIncompleteError
from band.core.retention.cleanup import task_failure
from band.platform.event import PlatformEvent
from band.runtime.execution import Execution

logger = logging.getLogger(__name__)

# Without a new signal a room needs at most: retire its old execution, start a
# new one, and retire that start when a stop overtook it.
MAX_STEPS_PER_SIGNAL = 3

# Events a wanted room receives before its execution is routed. Messages are
# also recovered by the /next resync; the rest (participants, room updates)
# exist only here.
MAX_HELD_EVENTS = 256
# A full queue plus as many arrivals during its delivery; beyond that the
# room is flooded and the remainder is dropped for /next to recover messages.
MAX_DELIVERY_STEPS = 2 * MAX_HELD_EVENTS


class RoomState(StrEnum):
    IDLE = "idle"
    """No step in flight and not serving; may still own a failed execution."""
    STARTING = "starting"
    SERVING = "serving"
    RETIRING = "retiring"


def _new_future() -> asyncio.Future[bool]:
    future = asyncio.get_running_loop().create_future()
    # A failure nobody awaits is already logged; don't warn again on GC.
    future.add_done_callback(lambda done: done.cancelled() or done.exception())
    return future


@dataclass
class Convergence:
    """One attempt to bring a room to its intent; waiters await ``done``."""

    done: asyncio.Future[bool] = field(default_factory=_new_future)
    graceful: bool = True


@dataclass
class Room:
    """One room's membership intent and the execution it owns.

    ``execution`` stays owned from construction until its cleanup succeeds.
    ``generation`` counts withdrawals, so a start that outlived one is retired
    instead of routed.
    """

    wanted: bool = False
    generation: int = 0
    state: RoomState = RoomState.IDLE
    execution: Execution | None = None
    stop_timeout: float | None = None
    steps_left: int = MAX_STEPS_PER_SIGNAL
    step: asyncio.Task[None] | None = None
    convergence: Convergence | None = None
    held: deque[PlatformEvent] = field(
        default_factory=lambda: deque(maxlen=MAX_HELD_EVENTS)
    )


async def request_resync(room_id: str, execution: Execution) -> None:
    """Ask an execution to re-poll /next, if it supports that."""
    if not hasattr(execution, "request_resync"):
        logger.debug(
            "Execution for room %s does not support request_resync, skipping",
            room_id,
        )
        return
    try:
        await execution.request_resync()
    except Exception as e:  # noqa: BLE001 -- runtime loop must log and continue rather than crash the agent process
        logger.warning("Failed to request resync for room %s: %s", room_id, e)


class RoomExecutions:
    """Converges every room's execution to its membership intent.

    Only ``routed`` receives work and control. Every execution, routed or not,
    stays owned here until its cleanup succeeds.
    """

    def __init__(
        self,
        *,
        build: Callable[[str], Execution],
        release: Callable[[str], Awaitable[None]],
    ) -> None:
        self.routed: dict[str, Execution] = {}
        self.accepting = True
        # The grace every retirement gets once the owner stops accepting.
        self._closing_timeout: float | None = None
        self._rooms: dict[str, Room] = {}
        self._build = build
        # Room-scoped cleanup that runs after its execution stopped.
        self._release = release

    def close(self, *, timeout: float | None) -> None:
        """Stop admitting rooms. Every retirement from now on, including one
        a finishing start triggers before its room is destroyed, waits up to
        ``timeout``."""
        self.accepting = False
        self._closing_timeout = timeout

    def room_ids(self) -> list[str]:
        return list(self._rooms)

    def wanted(self, room_id: str) -> bool:
        room = self._rooms.get(room_id)
        return room is not None and room.wanted

    def serving(self, room_id: str) -> bool:
        room = self._rooms.get(room_id)
        return (
            room is not None
            and room.execution is not None
            and self.routed.get(room_id) is room.execution
        )

    def pending(self, room_id: str) -> asyncio.Future[bool] | None:
        """The room's unfinished convergence, if one is under way."""
        room = self._rooms.get(room_id)
        if room is None or room.convergence is None:
            return None
        return room.convergence.done

    @property
    def unsettled(self) -> list[str]:
        """Rooms owning an execution that is not serving them: startup or
        cleanup not yet settled. Rooms still serving are not counted, so an
        empty list does not mean every room has stopped."""
        return [
            room_id
            for room_id, room in self._rooms.items()
            if room.execution is not None and not self.serving(room_id)
        ]

    async def admit(self, room_id: str) -> Execution | None:
        """Admit a room and wait for its startup.

        Returns None without waiting when an earlier execution must be cleaned
        up first; admission then completes in the same convergence.
        """
        if not self.accepting:
            raise RuntimeError("Agent runtime is stopping or stopped")
        if self.serving(room_id):
            logger.debug("Execution already exists for room %s", room_id)
            return self.routed[room_id]
        previous = self._rooms.get(room_id)
        cleanup_first = previous is not None and previous.execution is not None
        pending = self.request_admission(room_id)
        if cleanup_first:
            return None
        if pending is not None:
            await asyncio.shield(pending)
        return self.routed.get(room_id)

    def request_admission(self, room_id: str) -> asyncio.Future[bool] | None:
        room = self._rooms.setdefault(room_id, Room())
        room.wanted = True
        return self._signal(room_id, room)

    def hold(self, room_id: str, event: PlatformEvent) -> None:
        """Keep an event for a wanted room until its execution is routed."""
        if (room := self._rooms.get(room_id)) is None or not room.wanted:
            logger.warning("No execution for room %s, event dropped", room_id)
            return
        if len(room.held) == room.held.maxlen:
            logger.warning(
                "Room %s held %d events before routing; dropping the oldest",
                room_id,
                MAX_HELD_EVENTS,
            )
        room.held.append(event)
        self._signal(room_id, room)

    def withdraw(self, room_id: str) -> None:
        """Fence a wanted room at once and request its owned cleanup."""
        if (room := self._rooms.get(room_id)) is None or not room.wanted:
            return
        self._fence(room_id, room)
        self._signal(room_id, room)

    def converge(self, room_id: str) -> None:
        """Retry a room toward its intent, e.g. after a failed step."""
        if (room := self._rooms.get(room_id)) is not None:
            self._signal(room_id, room)

    def converge_unserved(self) -> None:
        """Retry every room whose startup or cleanup has not settled."""
        for room_id, room in list(self._rooms.items()):
            if not self.serving(room_id):
                self._signal(room_id, room)

    async def destroy(self, room_id: str, timeout: float | None = None) -> bool:
        """Withdraw a room and wait for its owned cleanup.

        A failure raises; the execution stays owned and a later leave,
        rejoin, reconnect or stop retries it.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        if (room := self._rooms.get(room_id)) is None:
            return True
        self._fence(room_id, room)
        room.stop_timeout = timeout
        if (pending := self._signal(room_id, room)) is None:
            return True
        return await asyncio.shield(pending)

    def _fence(self, room_id: str, room: Room) -> None:
        """Withdraw membership: no admission completes and no work routes."""
        self.routed.pop(room_id, None)
        room.wanted = False
        room.generation += 1
        room.stop_timeout = None
        room.held.clear()

    def _signal(self, room_id: str, room: Room) -> asyncio.Future[bool] | None:
        room.steps_left = MAX_STEPS_PER_SIGNAL
        self._advance(room_id, room)
        return room.convergence.done if room.convergence is not None else None

    def _advance(self, room_id: str, room: Room) -> None:
        """Take the one step the room's state and intent call for, if any."""
        match room.state, self._admits(room), room.execution:
            case RoomState.STARTING | RoomState.RETIRING, _, _:
                pass  # the step in flight advances the room when it ends
            case RoomState.SERVING, True, _:
                self._settle(room)
            case _, False, None:
                self._forget(room_id, room)
            case _, True, None:
                self._step(room_id, room, RoomState.STARTING, self._start)
            case _, _, execution if execution is not None:
                self.routed.pop(room_id, None)
                self._step(
                    room_id,
                    room,
                    RoomState.RETIRING,
                    partial(self._retire, execution=execution),
                )

    def _step(
        self,
        room_id: str,
        room: Room,
        state: RoomState,
        run: Callable[[str, Room], Coroutine[Any, Any, None]],
    ) -> None:
        if room.convergence is None:
            room.convergence = Convergence()
        if room.steps_left == 0:
            self._fail(
                room_id,
                room,
                CleanupIncompleteError(
                    f"Room {room_id} lifecycle exceeded {MAX_STEPS_PER_SIGNAL} "
                    "steps without a new signal"
                ),
            )
            return
        room.steps_left -= 1
        room.state = state
        room.step = asyncio.create_task(run(room_id, room))
        room.step.add_done_callback(partial(self._step_done, room_id, room))

    def _step_done(self, room_id: str, room: Room, step: asyncio.Task[None]) -> None:
        room.step = None
        if (error := task_failure(step)) is None:
            self._advance(room_id, room)
            return
        failed_step, room.state = room.state, RoomState.IDLE
        if failed_step == RoomState.STARTING and not self._admits(room):
            # Nobody wants this room any more; retiring what the failed start
            # built is the outcome its waiters asked for.
            logger.warning("Room %s start failed after withdrawal: %s", room_id, error)
            self._advance(room_id, room)
            return
        self._fail(room_id, room, error)

    def _admits(self, room: Room) -> bool:
        return room.wanted and self.accepting

    def _current(self, room: Room, generation: int) -> bool:
        """The intent a start began under still stands."""
        return self._admits(room) and room.generation == generation

    def _settle(self, room: Room) -> None:
        if room.convergence is not None:
            room.convergence.done.set_result(room.convergence.graceful)
            room.convergence = None

    def _fail(self, room_id: str, room: Room, error: BaseException) -> None:
        logger.warning("Room %s lifecycle remains pending: %s", room_id, error)
        if room.convergence is not None:
            room.convergence.done.set_exception(error)
            room.convergence = None

    def _forget(self, room_id: str, room: Room) -> None:
        if self._rooms.get(room_id) is room:
            del self._rooms[room_id]
        self._settle(room)

    async def _start(self, room_id: str, room: Room) -> None:
        """Start an owned execution; route it only if the admission it started
        under still stands.

        Every await here can outlive a leave, stop or rejoin, so the admission
        is re-read after each one, and routing follows its last check with no
        await between. A start that loses its admission is left unrouted for
        this owner to retire before any replacement starts. Startup has no
        time bound here: leave and stop wait for it to settle, then retire it.
        """
        generation = room.generation
        room.execution = execution = self._build(room_id)
        await execution.start()
        await self._deliver_held(room_id, room, execution, generation)
        if self._current(room, generation):
            self.routed[room_id] = execution
            logger.debug("Created execution for room %s", room_id)
            await request_resync(room_id, execution)
        room.state = RoomState.SERVING if self.serving(room_id) else RoomState.IDLE

    async def _deliver_held(
        self, room_id: str, room: Room, execution: Execution, generation: int
    ) -> None:
        """Hand held events to the starting execution, oldest first, while its
        admission stands.

        Events arriving meanwhile are held too, so live routing begins only
        once the queue is empty. Delivery stops as soon as the admission is
        withdrawn; events held after a rejoin stay for the fresh execution.
        Each batch leaves the bounded queue before any await, so a later
        ``hold`` cannot evict an event already accepted for this admission.
        A withdrawn admission drops the undelivered tail of that batch. The
        tail lives only in the local list, so putting it back would deliver
        it to the next execution.
        """
        delivered = 0
        while room.held and self._current(room, generation):
            if delivered >= MAX_DELIVERY_STEPS:
                self._drop_held(room_id, room)
                return
            batch = list(room.held)
            room.held.clear()
            for index, event in enumerate(batch):
                if not self._current(room, generation):
                    self._discard_undelivered(room_id, len(batch) - index)
                    return
                if delivered >= MAX_DELIVERY_STEPS:
                    self._drop_held(room_id, room, len(batch) - index)
                    return
                await execution.on_event(event)
                delivered += 1

    def _discard_undelivered(self, room_id: str, count: int) -> None:
        if count == 0:
            return
        logger.warning(
            "Room %s left during startup delivery; discarding %d undelivered",
            room_id,
            count,
        )

    def _drop_held(self, room_id: str, room: Room, extra: int = 0) -> None:
        pending = len(room.held) + extra
        logger.warning(
            "Room %s kept receiving events during startup; dropping %d held",
            room_id,
            pending,
        )
        room.held.clear()

    async def _retire(self, room_id: str, room: Room, *, execution: Execution) -> None:
        timeout = room.stop_timeout if self.accepting else self._closing_timeout
        graceful = await execution.stop(timeout=timeout)
        await self._release(room_id)
        room.execution = None
        room.state = RoomState.IDLE
        if room.convergence is not None:
            room.convergence.graceful = room.convergence.graceful and graceful
        logger.debug("Destroyed execution for room %s", room_id)
