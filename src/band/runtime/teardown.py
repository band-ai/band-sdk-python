"""RoomTeardown - stops one execution and cleans its room up, until both succeed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from band.runtime.execution import Execution


class RoomTeardown:
    """Owns one execution from the moment it must be released.

    Callers share one in-flight attempt, and a cancelled caller leaves it
    running for the next. An attempt whose stop or cleanup raised propagates
    to its callers, and the next ``run`` starts a fresh one; a retry after a
    failed cleanup does not stop the execution again.
    """

    def __init__(
        self,
        execution: Execution,
        cleanup: Callable[[], Awaitable[None]],
        forget: Callable[[], None],
    ) -> None:
        self._execution = execution
        self._cleanup = cleanup
        self._forget = forget
        self._attempt: asyncio.Task[bool] | None = None
        self._graceful_stop: asyncio.Task[bool] | None = None
        # Sticky once any caller asks for an immediate stop, so a later
        # graceful request cannot extend it.
        self._immediate = False
        # The stop's graceful result; a cleanup retry reuses it.
        self._stopped: bool | None = None

    async def run(self, timeout: float | None) -> bool:
        """Join the in-flight attempt, or start one. True if stopped gracefully."""
        if timeout is None:
            self._hurry()
        attempt = self._attempt
        if attempt is None or (attempt.done() and attempt.exception() is not None):
            attempt = self._attempt = asyncio.ensure_future(self._finish(timeout))
        return await asyncio.shield(attempt)

    def _hurry(self) -> None:
        self._immediate = True
        if self._graceful_stop is not None:
            self._graceful_stop.cancel()

    async def _finish(self, timeout: float | None) -> bool:
        stopped = self._stopped
        if stopped is None:
            stopped = self._stopped = await self._stop(timeout)
        await self._cleanup()
        self._forget()
        return stopped

    async def _stop(self, timeout: float | None) -> bool:
        """Stop the execution; an immediate request preempts a graceful stop.

        Only one ``stop()`` call runs at a time: a preempted graceful stop is
        cancelled first, then followed by ``stop(timeout=None)``.
        """
        if timeout is None or self._immediate:
            return await self._execution.stop(timeout=None)
        graceful_stop = self._graceful_stop = asyncio.ensure_future(
            self._execution.stop(timeout=timeout)
        )
        await asyncio.wait({graceful_stop})
        self._graceful_stop = None
        if graceful_stop.cancelled():
            await self._execution.stop(timeout=None)
            return False
        return graceful_stop.result()
