"""Host-facing handshake results and custody of throwaway harness probes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict

from band.core.exceptions import CleanupIncompleteError
from band.core.retention.cleanup import rejoin_owned, report_owned

_T = TypeVar("_T")
_Close = Callable[[], Awaitable[None]]


class PreflightResult(BaseModel):
    """Whether a harness answered its handshake, not proof of inference access."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    reason: str | None = None
    remedy: str | None = None

    @classmethod
    def passed(cls) -> PreflightResult:
        return cls(ok=True)

    @classmethod
    def failed(cls, reason: str, remedy: str) -> PreflightResult:
        return cls(ok=False, reason=reason, remedy=remedy)


class HarnessProbe:
    """One adapter's serialized probes and retryable resource cleanup.

    Operations register resource closers before starting their transports. A
    timeout bounds reporting, not ownership: cleanup joins late startup before
    closing anything it may still publish. A later probe or adapter cleanup
    rejoins that same work and retries failed closers.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._operation: asyncio.Task[Any] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._closers: list[_Close] = []
        self._cancel_requested = False

    def own(self, close: _Close) -> None:
        """Retain an idempotent closer before awaiting resource startup."""
        if self._operation is None or self._operation.done():
            raise RuntimeError("A harness resource must belong to a running probe")
        self._closers.append(close)

    async def run(
        self,
        operation: Callable[[], Awaitable[_T]],
        *,
        timeout_s: float,
    ) -> _T:
        """Run metadata discovery with separate operation and cleanup budgets."""
        async with self._lock:
            await self.close(timeout_s=timeout_s)
            enrolled = asyncio.get_running_loop().create_future()

            async def invoke() -> _T:
                await enrolled
                return await operation()

            task = asyncio.create_task(invoke())
            self._operation = task
            self._cleanup = None
            self._cancel_requested = False
            enrolled.set_result(None)
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout_s)
            except BaseException as primary:
                try:
                    await self.close(timeout_s=timeout_s)
                except BaseException as cleanup:
                    if isinstance(primary, asyncio.CancelledError):
                        raise primary from cleanup
                    raise cleanup from primary
                raise
            await self.close(timeout_s=timeout_s)
            return result

    async def close(self, *, timeout_s: float = 60.0) -> None:
        """Join or retry settlement without abandoning an unfinished transport."""
        if self._operation is None and not self._closers:
            return
        self._cleanup = rejoin_owned(
            self._cleanup,
            self._finish,
            label="Harness probe cleanup",
            diagnostic_details=False,
        )
        await report_owned(self._cleanup, timeout_s)

    async def _finish(self) -> None:
        if (operation := self._operation) is not None:
            if not operation.done() and not self._cancel_requested:
                self._cancel_requested = True
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

        retained: list[_Close] = []
        failures: list[Exception] = []
        for close in reversed(self._closers):
            try:
                await close()
            except Exception as error:  # noqa: BLE001 -- attempt every closer while retaining each failed resource
                retained.append(close)
                failures.append(error)
        self._closers = list(reversed(retained))
        if failures:
            raise CleanupIncompleteError("Harness probe resources remain owned") from (
                ExceptionGroup("Harness probe cleanup failed", failures)
            )
        self._operation = None
        self._cancel_requested = False


__all__ = ["PreflightResult"]
