"""Each room's owned execution: admission, routing and retirement."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from band.runtime.execution import Execution

logger = logging.getLogger(__name__)


@dataclass
class RoomLifecycle:
    """One room's membership intent and the execution it owns.

    ``execution`` stays owned from construction until its cleanup succeeds;
    it receives work only while it is also the room's routed execution.
    ``task`` is the room's single owned startup/retirement operation, and
    ``intent`` counts withdrawals so a startup that outlived one is retired.
    """

    wanted: bool = False
    intent: int = 0
    execution: Execution | None = None
    task: asyncio.Task[bool] | None = None


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
        self._rooms: dict[str, RoomLifecycle] = {}
        self._build = build
        # Room-scoped cleanup that runs after its execution stopped.
        self._release = release

    def record(self, room_id: str) -> RoomLifecycle | None:
        return self._rooms.get(room_id)

    def room_ids(self) -> list[str]:
        return list(self._rooms)

    def wanted(self, room_id: str) -> bool:
        record = self._rooms.get(room_id)
        return record is not None and record.wanted

    def serving(self, room_id: str) -> bool:
        record = self._rooms.get(room_id)
        execution = self.routed.get(room_id)
        return (
            record is not None
            and execution is not None
            and execution is record.execution
        )

    @property
    def unsettled(self) -> list[str]:
        """Rooms owning an execution that is not serving them: startup or
        cleanup not yet settled. Rooms still serving are not counted, so an
        empty list does not mean every room has stopped."""
        return [
            room_id
            for room_id, record in self._rooms.items()
            if record.execution is not None and not self.serving(room_id)
        ]

    async def admit(self, room_id: str) -> Execution | None:
        """Admit a room through its owned lifecycle.

        Waits for a fresh room's startup. Returns None without waiting when an
        earlier execution must be cleaned up first; admission then completes
        through the same owned operation once that cleanup succeeds.
        """
        if not self.accepting:
            raise RuntimeError("Agent runtime is stopping or stopped")
        if self.serving(room_id):
            logger.debug("Execution already exists for room %s", room_id)
            return self.routed[room_id]
        previous = self._rooms.get(room_id)
        cleanup_first = previous is not None and previous.execution is not None
        lifecycle = self.request_admission(room_id)
        if cleanup_first:
            return None
        await asyncio.shield(lifecycle)
        return self.routed.get(room_id)

    def request_admission(self, room_id: str) -> asyncio.Task[bool]:
        record = self._rooms.setdefault(room_id, RoomLifecycle())
        record.wanted = True
        return self.converge(room_id)

    def withdraw(self, room_id: str) -> None:
        """Fence a wanted room at once and request its owned cleanup."""
        if not self.wanted(room_id):
            return
        self._fence(room_id)
        self.converge(room_id)

    def converge_unserved(self) -> None:
        """Retry every room whose startup or cleanup has not settled."""
        for room_id in self.room_ids():
            if not self.serving(room_id):
                self.converge(room_id)

    async def destroy(self, room_id: str, timeout: float | None = None) -> bool:
        """Withdraw a room and wait for its owned cleanup.

        A failure raises; the execution stays owned and a later leave,
        rejoin, reconnect or stop retries it.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        record = self._fence(room_id)
        graceful = True
        while record is not None and self._rooms.get(room_id) is record:
            lifecycle = self.converge(room_id, timeout=timeout)
            graceful = await asyncio.shield(lifecycle) and graceful
        return graceful

    def converge(
        self, room_id: str, *, timeout: float | None = None
    ) -> asyncio.Task[bool]:
        """Join the room's owned operation, or start one after the last settled."""
        record = self._rooms[room_id]
        if record.task is None or record.task.done():
            record.task = asyncio.create_task(self._reconcile(room_id, record, timeout))
            record.task.add_done_callback(self._observe_lifecycle)
        return record.task

    def _fence(self, room_id: str) -> RoomLifecycle | None:
        """Withdraw membership: no admission completes and no work routes."""
        self.routed.pop(room_id, None)
        record = self._rooms.get(room_id)
        if record is not None:
            record.wanted = False
            record.intent += 1
        return record

    @staticmethod
    def _observe_lifecycle(lifecycle: asyncio.Task[bool]) -> None:
        if not lifecycle.cancelled() and (error := lifecycle.exception()):
            logger.warning("Room lifecycle remains pending: %s", error)

    async def _reconcile(
        self, room_id: str, record: RoomLifecycle, timeout: float | None
    ) -> bool:
        """Converge the room's execution to its current membership intent.

        Intent is re-read after every await, so a leave or stop during a held
        startup retires that execution instead of publishing it.
        """
        graceful = True
        while True:
            admit = record.wanted and self.accepting
            if self.serving(room_id):
                if admit:
                    return graceful
                self.routed.pop(room_id)
            if (execution := record.execution) is not None:
                retired = await self._retire(room_id, record, execution, timeout)
                graceful = retired and graceful
            elif admit:
                await self._admit(room_id, record)
            else:
                self._forget(room_id, record)
                return graceful

    def _forget(self, room_id: str, record: RoomLifecycle) -> None:
        if self._rooms.get(room_id) is record:
            del self._rooms[room_id]

    async def _admit(self, room_id: str, record: RoomLifecycle) -> None:
        """Start an owned execution; route it only if the membership intent it
        started under still stands.

        Events arriving before routing are dropped unclaimed, so a routed
        execution always resyncs /next to recover them. Startup has no time
        bound here: leave and stop wait for it to settle, then retire it.
        """
        intent = record.intent
        execution = self._build(room_id)
        record.execution = execution
        await execution.start()
        if not (record.wanted and self.accepting and record.intent == intent):
            return
        self.routed[room_id] = execution
        logger.debug("Created execution for room %s", room_id)
        await request_resync(room_id, execution)

    async def _retire(
        self,
        room_id: str,
        record: RoomLifecycle,
        execution: Execution,
        timeout: float | None,
    ) -> bool:
        graceful = await execution.stop(timeout=timeout)
        await self._release(room_id)
        record.execution = None
        logger.debug("Destroyed execution for room %s", room_id)
        return graceful
