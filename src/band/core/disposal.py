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
        position = max(self.callbacks, default=0) + 1
        self.callbacks[position] = callback

    @property
    def requires_ownership(self) -> bool:
        return bool(self.callbacks) or self.owner is not None

    def check_owner(self, owner: object, *, exclusive: bool) -> None:
        if self.finished and self.requires_ownership:
            raise BandConfigError(
                "Adapter resources are disposed; build fresh resources, tool closures, adapter and host"
            )
        foreign_owner = self.owner is not None and self.owner is not owner
        unowned_framework = self.owner is None and self.builtin_started
        if exclusive and not self.finished and (foreign_owner or unowned_framework):
            raise BandConfigError(
                "Adapter is already in use by another host; use that host or build a separate adapter"
            )
        if self.disposing and not self.finished:
            raise CleanupIncompleteError(
                "Adapter cleanup is incomplete; retry stop() or shutdown() on the same host"
            )

    def accept(self, owner: object, *, exclusive: bool) -> None:
        if self.finished:
            self.builtin_started = False
            self.builtin_done = False
            self.disposing = False
            self.finished = False
        if exclusive:
            self.owner = owner
        self.sealed = True

    def arm_framework(self) -> None:
        self.builtin_started = True
        self.builtin_done = False

    async def finish_framework(self, cleanup: CleanupCallback) -> None:
        if self.builtin_done:
            return
        if self.builtin_started or not self.sealed:
            await cleanup()
        self.builtin_done = True

    async def finish_resources(self, finalize: CleanupCallback) -> None:
        if self.finished:
            return
        await finalize()
        await self.run_callbacks()
        self.finished = True

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
