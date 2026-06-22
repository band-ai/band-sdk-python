"""Boolean working-state keep-alive reporter.

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
a per-POST timeout to satisfy this.

Cadence accuracy: the keep-alive sleeps ``keep_alive_seconds`` and *then* reports,
so the effective interval between successful refreshes is
``keep_alive_seconds + report-latency``. The platform-TTL headroom is preserved by
the ``cadence < TTL/2`` guard on ``SessionConfig`` (with the per-POST timeout kept
below the cadence), so even one missed/slow ping stays within the TTL window.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

ReportFn = Callable[[bool], Awaitable[object]]


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
                "keep_alive_seconds must be > 0 (got %s)" % keep_alive_seconds
            )
        self._report = report
        self._keep_alive_seconds = keep_alive_seconds
        self._max_working_state_seconds = max_working_state_seconds
        self._enabled = enabled

        self._active = False
        self._started_at: float | None = None
        self._task: asyncio.Task[None] | None = None
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
        self._active = True
        self._started_at = asyncio.get_running_loop().time()
        await self._safe_report(True)
        self._task = asyncio.create_task(
            self._keep_alive(), name="working-state-keepalive"
        )

    async def stop(self) -> None:
        """End the reasoning cycle: cancel keep-alive and send ``working: false``.

        The false-send is shielded so that when the cycle ends via cancellation
        (the caller is being cancelled), the authoritative ``false`` still lands
        instead of being swallowed by the propagating ``CancelledError``.
        """
        if not self._enabled or not self._active:
            return
        self._active = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        send = asyncio.ensure_future(self._safe_report(False))
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
        """Report under the serialization lock; never raise."""
        async with self._lock:
            try:
                await self._report(working)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "working-state report failed (working=%s); swallowing", working
                )
