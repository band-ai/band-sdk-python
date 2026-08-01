"""Cancellation-token implementations for ``RunContext``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from band.core.protocols import CancellationToken


@dataclass
class NeverCancelled:
    """Cancellation token that never fires."""

    @property
    def cancelled(self) -> bool:
        return False

    def throw_if_cancelled(self) -> None:
        return None


@dataclass
class FlagCancellation:
    """Flag-based cancellation token."""

    _cancelled: bool = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def throw_if_cancelled(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError()


@dataclass
class AnyCancellation:
    """Cancelled as soon as any of its tokens is.

    Lets an owner keep a lever it can pull — a ``FlagCancellation`` of its own
    — without disowning the token a caller supplied, which is usually the only
    one that knows about the caller's own interrupts.
    """

    tokens: tuple[CancellationToken, ...]

    @property
    def cancelled(self) -> bool:
        return any(token.cancelled for token in self.tokens)

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError()


@dataclass
class ExecutionCancellation:
    """Reads ``ExecutionContext._interrupt_kind`` / ``_pending_interrupt``."""

    _ctx: object

    @property
    def cancelled(self) -> bool:
        kind = getattr(self._ctx, "_interrupt_kind", None)
        pending = getattr(self._ctx, "_pending_interrupt", None)
        return kind is not None or pending is not None

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError()
