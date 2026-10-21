"""Keep native CrewAI execution and its thread-submitted tools in one turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from band.core.exceptions import CleanupIncompleteError, InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import TurnTask, bind_turn_task, terminal_deadline

logger = logging.getLogger(__name__)
Result = TypeVar("Result")


def _observe_native(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.debug("CrewAI native execution failed: %s", error, exc_info=error)


class ToolTurn:
    """Retain native execution and the tools its worker thread submitted."""

    def __init__(self, tools: AgentToolsProtocol) -> None:
        self._tools = tools
        self._closed = False
        self._native: asyncio.Task[Any] | None = None
        self._owners: set[TurnTask] = set()

    @property
    def settled(self) -> bool:
        return (
            self._closed
            and (self._native is None or self._native.done())
            and all(owner.task.done() for owner in self._owners)
        )

    def check_active(self) -> None:
        if self._closed:
            raise InputNotAccepted("CrewAI turn has ended; new work is not accepted")

    async def execute(
        self, operation: Callable[[], Coroutine[Any, Any, Result]]
    ) -> Result:
        self.check_active()
        if self._native is not None:
            raise RuntimeError("CrewAI turn execution has already started")
        task = asyncio.create_task(operation())
        self._native = task
        task.add_done_callback(_observe_native)
        # Cancelling kickoff abandons CrewAI's synchronous worker thread.
        return await asyncio.shield(task)

    async def run_tool(
        self, operation: Callable[[], Coroutine[Any, Any, Result]]
    ) -> Result:
        self.check_active()
        task = asyncio.create_task(operation())
        owner = bind_turn_task(self._tools, task)
        self._owners.add(owner)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._owners.discard(owner)

    async def abort(self) -> None:
        """Refuse new work and cancel tools without abandoning the native worker."""
        self._closed = True
        owners = tuple(self._owners)
        deadline = terminal_deadline()
        for owner in owners:
            owner.retry(deadline=deadline)
        try:
            results = await asyncio.gather(*(owner.drain() for owner in owners))
            if not all(results):
                raise CleanupIncompleteError(
                    "CrewAI tools have not settled; retry adapter cleanup"
                )
        finally:
            self._owners = {owner for owner in self._owners if not owner.task.done()}

    async def close(self, *, cancel: bool) -> None:
        """Join native execution before releasing resources; a cancelled waiter may retry."""
        self._closed = True
        if cancel:
            await self.abort()
        pending = {owner.task for owner in self._owners if not owner.task.done()}
        if self._native is not None and not self._native.done():
            pending.add(self._native)
        if pending:
            await asyncio.wait(pending)


def _forget_settled(turns: dict[str, set[ToolTurn]], room_id: str) -> None:
    owners = turns.get(room_id)
    if owners is None:
        return
    owners.difference_update(turn for turn in tuple(owners) if turn.settled)
    if not owners:
        del turns[room_id]


@asynccontextmanager
async def tool_turn(
    turns: dict[str, set[ToolTurn]], room_id: str, tools: AgentToolsProtocol
) -> AsyncIterator[ToolTurn]:
    turn = ToolTurn(tools)
    turns.setdefault(room_id, set()).add(turn)
    try:
        try:
            yield turn
        except BaseException as error:
            try:
                await turn.abort()
            except (asyncio.CancelledError, CleanupIncompleteError):
                error.add_note(
                    "CrewAI work remains pending; retain this adapter and retry cleanup"
                )
            raise
        else:
            await turn.close(cancel=False)
    finally:
        _forget_settled(turns, room_id)


async def settle_tool_turns(
    turns: dict[str, set[ToolTurn]], *, room_id: str | None = None
) -> None:
    rooms = tuple(turns) if room_id is None else (room_id,)
    owners = tuple(turn for room in rooms for turn in turns.get(room, ()))
    try:
        results = await asyncio.gather(
            *(turn.close(cancel=True) for turn in owners), return_exceptions=True
        )
    finally:
        for room in rooms:
            _forget_settled(turns, room)
    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        raise BaseExceptionGroup("CrewAI work cleanup remains incomplete", failures)
