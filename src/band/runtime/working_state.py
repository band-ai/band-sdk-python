"""Working-state keep-alive reporter.

Owns the per-room "is the agent working" signal for a single reasoning cycle:
sends ``working: true`` when a cycle starts, refreshes it on a keep-alive
cadence while the cycle is active, and sends an authoritative ``working: false``
when the cycle ends. The platform applies a short TTL, so if refreshes stop for
any reason (crash, disconnect, loop-block) the indicator clears on its own — the
keep-alive is a *liveness* signal, not a correctness signal.

The reporter is transport-agnostic: it is given an async ``report(working)``
callback (wired to ``BandLink.report_activity`` by the runtime) and never raises
into the caller's reasoning loop.

Contract: ``report`` MUST be time-bounded. The reporter awaits it (including the
authoritative false-send during teardown), so an unbounded callback could wedge
cycle teardown and cancellation propagation. ``BandLink.report_activity`` applies
one deadline per report, covering the POST and its compatibility resend.

The keep-alive task is the only sender of ``working: true`` after the first.
``refresh()`` wakes it to report a changed activity now, so a caller never waits
on the network and changes made while a report is in flight coalesce.

Cadence accuracy: the keep-alive waits ``keep_alive_seconds`` (or a wake) and
*then* reports, so the effective interval between successful refreshes is
``keep_alive_seconds + report-latency``. The platform-TTL headroom is preserved by
the ``cadence < TTL/2`` guard on ``SessionConfig`` (with the report deadline kept
below the cadence), so even one missed/slow ping stays within the TTL window.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

logger = logging.getLogger(__name__)

WORKING_CAP_PAUSED = (
    "working-state reporting cap reached; pausing keep-alive pings "
    "(platform TTL will clear the indicator)"
)

ReportFn = Callable[[bool], Awaitable[object]]


def _retrieve_task_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


def project_working(deadlines: tuple[float | None, ...], now: float) -> bool | None:
    """Report eligible execution, pause capped execution, or clear absent authority."""
    if not deadlines:
        return False
    return True if any(end is None or now < end for end in deadlines) else None


class WorkingStateReporter:
    """Manages the working signal and its activity for one execution (agent + room)."""

    def __init__(
        self,
        report: ReportFn,
        *,
        keep_alive_seconds: float = 3.0,
        project: Callable[[], bool | None] | None = None,
        enabled: bool = True,
    ) -> None:
        if keep_alive_seconds <= 0:
            raise ValueError(
                f"keep_alive_seconds must be > 0 (got {keep_alive_seconds})"
            )
        self._report = report
        self._keep_alive_seconds = keep_alive_seconds
        self._project = project
        self._enabled = enabled

        self._active = False
        self._task: asyncio.Task[None] | None = None
        self._false_send: asyncio.Future[bool | None] | None = None
        self._wake = asyncio.Event()
        # Serializes every report() call (true / keep-alive / false) so they can
        # never overlap on the wire — guarantees the platform sees them in order
        # (boolean payload carries no sequence number to disambiguate).
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Begin a reasoning cycle: send ``working: true`` and arm keep-alive."""
        if not self._enabled or self._active:
            # Re-entrant start while active is a no-op: the signal is already
            # alive and the keep-alive task is already running.
            return
        await self._finish_false_send()
        self._active = True
        self._wake.clear()
        await self._safe_report(True)
        # A false armed during the first report ends the cycle before it ran.
        if self._active:
            self._task = asyncio.create_task(
                self._keep_alive(), name="working-state-keepalive"
            )

    def refresh(self) -> None:
        """Report the current activity now instead of at the next keep-alive."""
        self._wake.set()

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

    def _arm_false_report(self) -> asyncio.Task[None] | None:
        self._active = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._false_send = asyncio.ensure_future(self._safe_report(False))
        return task

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
            # callback has a per-report deadline) so it still completes on the loop;
            # and the platform TTL backstops the clear regardless. Not handled
            # explicitly — the double-cancel teardown window is vanishingly rare.
            await send
            raise
        finally:
            if send.done():
                self._false_send = None

    async def _keep_alive(self) -> None:
        """Re-send ``working: true`` on cadence or wake until stopped or capped."""
        paused = False
        while True:
            with suppress(TimeoutError):
                async with asyncio.timeout(self._keep_alive_seconds):
                    await self._wake.wait()
            self._wake.clear()
            if not self._active:
                return
            if await self._safe_report(True) is None:
                if self._active and not paused:
                    logger.info(WORKING_CAP_PAUSED)
                    paused = True
                await self._wake.wait()
            else:
                paused = False

    async def _safe_report(self, working: bool) -> bool | None:
        """Report under the serialization lock; never raise."""
        async with self._lock:
            if working and not self._active:
                return None
            if working and self._project is not None:
                projected = self._project()
                if projected is None:
                    return None
                if not projected:
                    self.defer_false_report()
                    return False
            try:
                await self._report(working)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "working-state report failed (working=%s); swallowing", working
                )
            return working
