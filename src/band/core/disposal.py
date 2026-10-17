"""Per-instance progress for host-owned adapter disposal."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from band.core.exceptions import BandConfigError, CleanupIncompleteError

CleanupCallback = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


@dataclass
class AdapterDisposal:
    callbacks: dict[int, CleanupCallback] = field(default_factory=dict)
    registrations: int = 0
    owner: object | None = None
    sealed: bool = False
    builtin_started: bool = False
    builtin_done: bool = False
    disposing: bool = False
    finished: bool = False
    task: asyncio.Task[object] | None = None

    def register(self, callback: CleanupCallback) -> None:
        if not callable(callback):
            raise TypeError(
                "Pass a zero-argument async callable, such as client.aclose, not client.aclose()"
            )
        if self.sealed:
            raise BandConfigError("Register cleanup before starting the host")
        self.registrations += 1
        self.callbacks[self.registrations] = callback

    def check_owner(self, owner: object, *, exclusive: bool) -> None:
        if self.finished and exclusive:
            raise BandConfigError(
                "Adapter resources are disposed; build fresh resources, tool closures, adapter and host"
            )
        if exclusive and self.owner is not None and self.owner is not owner:
            raise BandConfigError(
                "Adapter resources belong to another host; use that host or build a separate adapter"
            )
        if self.disposing and not self.finished:
            raise CleanupIncompleteError(
                "Adapter cleanup is incomplete; retry stop() or shutdown() on the same host"
            )

    def accept(self, owner: object) -> None:
        if self.finished:
            self.builtin_started = self.builtin_done = self.disposing = (
                self.finished
            ) = False
        self.owner = owner
        self.sealed = True

    def refuse_recursive_shutdown(self) -> None:
        if self.task is not None and self.task is asyncio.current_task():
            raise BandConfigError(
                "Return from the cleanup callback; request host shutdown from outside this cleanup attempt"
            )

    async def run_callbacks(self) -> None:
        errors: list[Exception] = []
        for position, callback in reversed(list(self.callbacks.items())):
            logger.debug("Running adapter cleanup registration %d", position)
            try:
                result = callback()
                if not inspect.isawaitable(result):
                    raise TypeError(
                        "Cleanup callback must return an awaitable; pass a zero-argument async callable"
                    )
                await result
            except Exception as exc:  # noqa: BLE001 -- independent releases continue; failed entries stay pending
                name = getattr(callback, "__qualname__", type(callback).__name__)
                note = f"Adapter cleanup registration {position}: {name}"
                if note not in getattr(exc, "__notes__", ()):
                    exc.add_note(note)
                errors.append(exc)
            else:
                del self.callbacks[position]
        if errors:
            raise ExceptionGroup(
                "Adapter cleanup failed; retry stop() or shutdown() on the same host",
                errors,
            )
