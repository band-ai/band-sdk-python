"""Serialized working-state and names-only activity for one provider turn.

The boolean callback must be time bounded. It reads immutable detail under the
same lock as changes and heartbeats. Terminal false is shielded; platform TTL
backstops failed reports. A duration cap stops refreshes without cancelling work.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from inspect import getattr_static
from typing import Protocol

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.runtime.tools.types import BandTool

logger = logging.getLogger(__name__)

ReportFn = Callable[[bool], Awaitable[object]]


UNKNOWN_ACTIVITY_TOOL_NAME = "unknown"
REASONING = AgentActivityDetail(kind="reasoning")
MEMORY = AgentActivityDetail(kind="memory")
PERMISSION = AgentActivityDetail(kind="permission")


class ActivityObserver(Protocol):
    async def update(self, activity: AgentActivityDetail) -> None: ...

    async def set_manual_wait(self, waiting: bool) -> None: ...


def tool_call_activity(name: str | None) -> AgentActivityDetail:
    """The platform requires a name on every ``tool_call`` detail."""
    return AgentActivityDetail(
        kind="tool_call", name=name or UNKNOWN_ACTIVITY_TOOL_NAME
    )


def names_only(activity: AgentActivityDetail) -> AgentActivityDetail:
    """Keep only what the room may see: the kind and, for a tool, its name."""
    if activity.kind == "tool_call":
        return tool_call_activity(activity.name)
    return AgentActivityDetail(kind=activity.kind)


def _observer(tools: object) -> ActivityObserver | None:
    # Dynamic attribute proxies do not opt into runtime instrumentation.
    if getattr_static(tools, "_activity_observer", None) is None:
        return None
    return getattr(tools, "_activity_observer", None)


async def report_tool_activity(tools: object, name: str) -> None:
    if observer := _observer(tools):
        await observer.update(tool_call_activity(name))


async def end_tool_activity(tools: object) -> None:
    if observer := _observer(tools):
        await observer.update(REASONING)


async def report_memory_activity(tools: object) -> None:
    if observer := _observer(tools):
        await observer.update(MEMORY)


async def set_permission_wait(tools: object, waiting: bool) -> None:
    if observer := _observer(tools):
        await observer.set_manual_wait(waiting)


@asynccontextmanager
async def tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    try:
        await report_tool_activity(tools, name)
        yield
    finally:
        await end_tool_activity(tools)


@asynccontextmanager
async def model_tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    """Observe the dual-use event method only at its model invocation boundary."""
    if name == BandTool.SEND_EVENT:
        async with tool_activity(tools, name):
            yield
    else:
        yield


def _retrieve_task_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


class WorkingStateReporter:
    """Manages the boolean working signal for one execution (agent + room)."""

    def __init__(
        self,
        report: ReportFn,
        *,
        keep_alive_seconds: float = 3.0,
        max_working_state_seconds: float | None = None,
        enabled: bool = True,
    ) -> None:
        if keep_alive_seconds <= 0:
            raise ValueError(
                f"keep_alive_seconds must be > 0 (got {keep_alive_seconds})"
            )
        self._report = report
        self._keep_alive_seconds = keep_alive_seconds
        self._max_working_state_seconds = max_working_state_seconds
        self._enabled = enabled

        self._active = False
        self._ordinary = REASONING
        self._manual_wait = False
        self._started_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._false_send: asyncio.Future[None] | None = None
        # Serializes every report() call (true / keep-alive / false) so they can
        # never overlap on the wire — guarantees the platform sees them in order
        # (boolean payload carries no sequence number to disambiguate).
        self._lock = asyncio.Lock()

    @property
    def activity(self) -> AgentActivityDetail:
        """The immutable effective detail, read by the bool callback under its lock."""
        return PERMISSION if self._manual_wait else self._ordinary

    async def start(self) -> None:
        """Begin a cycle after the previous false; a re-entrant start is a no-op."""
        if not self._enabled:
            return
        await self._finish_false_send()
        async with self._lock:
            if self._active:
                return
            self._active = True

            self._reset_detail()
            self._started_at = asyncio.get_running_loop().time()
            await self._report_locked(True)
            if self._active:
                self._task = asyncio.create_task(
                    self._keep_alive(), name="working-state-keepalive"
                )

    async def update(
        self,
        *,
        current: Callable[[], bool],
        activity: AgentActivityDetail | None = None,
        waiting: bool | None = None,
    ) -> None:
        """Apply an observation; ``current`` rechecks ownership under the lock."""
        if not self._enabled:
            return
        async with self._lock:
            if not current() or not self._active or self._cap_reached():
                return
            previous = self.activity
            if activity is not None:
                self._ordinary = names_only(activity)
            if waiting is not None:
                self._manual_wait = waiting
            if self.activity != previous:
                await self._report_locked(True)

    def defer_false_report(self) -> None:
        """Start ``working: false`` and return without waiting for it.

        A closing execution joins the cancelled cycle on a budget shorter than
        the activity POST timeout. Waiting for the report on that cycle counts
        a still-legal report as unfinished cleanup. ``stop`` finishes the send.
        """
        if not self._enabled or not self._active:
            return
        task = self._arm_false_report()
        if task is not None:
            task.add_done_callback(_retrieve_task_error)

    async def stop(self) -> None:
        """End the reasoning cycle: cancel keep-alive and send ``working: false``.

        The false-send is shielded so that when the cycle ends via cancellation
        (the caller is being cancelled), the authoritative ``false`` still lands
        instead of being swallowed by the propagating ``CancelledError``.
        """
        if not self._enabled:
            return
        if not self._active:
            await self._finish_false_send()
            return
        task = self._arm_false_report()
        if task is not None and not task.done():
            # ``gather(return_exceptions=True)`` swallows the keep-alive task's
            # own CancelledError (returned as a result) while still propagating a
            # cancellation aimed at *us* — the caller awaiting stop(). A bare
            # ``suppress(CancelledError)`` here would also eat an external cancel
            # landing in this await window, silently un-cancelling the caller's
            # process loop and stalling shutdown (observed as a hang on Windows,
            # where scheduling makes the race routine).
            await asyncio.gather(task, return_exceptions=True)
        elif task is not None and not task.cancelled():
            # Already finished. Python 3.11's gather always schedules a done
            # callback on the task's loop, which raises once that loop has closed.
            task.exception()
        await self._finish_false_send()

    def _reset_detail(self) -> None:
        self._ordinary = AgentActivityDetail(kind="reasoning")
        self._manual_wait = False

    def _arm_false_report(self) -> asyncio.Task[None] | None:
        self._active = False

        self._reset_detail()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._false_send = asyncio.ensure_future(self._safe_report(False))
        return task

    def _reset_detail(self) -> None:
        self._ordinary = REASONING
        self._manual_wait = False

    async def _finish_false_send(self) -> None:
        send = self._false_send
        if send is None:
            return
        try:
            await asyncio.shield(send)
        except asyncio.CancelledError:
            # We were cancelled while awaiting; let the false-send finish so the
            # platform clears the indicator immediately rather than via TTL.
            # Accepted gap: a *second* cancel landing during this await can abandon
            # it, leaving `send` detached. `send` is time-bounded (the report
            # callback has a per-POST deadline) so it still completes on the loop;
            # and the platform TTL backstops the clear regardless. Not handled
            # explicitly — the double-cancel teardown window is vanishingly rare.
            await send
            raise
        finally:
            if send.done():
                self._false_send = None

    async def _keep_alive(self) -> None:
        """Re-send ``working: true`` on cadence until stopped or capped."""
        while True:
            await asyncio.sleep(self._keep_alive_seconds)
            if not self._active:
                return
            if self._cap_reached():
                # Stop lying past the cap: let the platform TTL flip the state.
                # We do NOT cancel the cycle and do NOT send false here — stop()
                # remains the authoritative clear when the cycle truly ends.
                logger.info(
                    "working-state max duration (%ss) reached; pausing keep-alive "
                    "pings (platform TTL will clear the indicator)",
                    self._max_working_state_seconds,
                )
                return
            await self._safe_report(True)

    def _cap_reached(self) -> bool:
        if self._max_working_state_seconds is None or self._started_at is None:
            return False
        elapsed = asyncio.get_running_loop().time() - self._started_at
        return elapsed >= self._max_working_state_seconds

    async def _safe_report(self, working: bool) -> None:
        async with self._lock:
            if working and (not self._active or self._cap_reached()):
                return
            await self._report_locked(working)

    async def _report_locked(self, working: bool) -> None:
        try:
            await self._report(working)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "working-state report failed (working=%s); swallowing", working
            )
