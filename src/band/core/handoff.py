"""Context commitment at the boundary between adapter setup and execution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum, auto


class ContextDisposition(Enum):
    PENDING = auto()
    CONSUMED = auto()
    RETAIN = auto()


# Immutable context-local state keeps detached tasks from changing their parent
# delivery's commitment, even when they inherit its context.
_context_disposition: ContextVar[ContextDisposition | None] = ContextVar(
    "context_disposition", default=None
)


class ContextDelivery:
    """The commitment decision inside one coordinator delivery scope."""

    @property
    def should_commit(self) -> bool:
        return _context_disposition.get() is not ContextDisposition.RETAIN


@contextmanager
def context_delivery() -> Iterator[ContextDelivery]:
    """Default to commitment unless setup proves context was not consumed."""
    token = _context_disposition.set(ContextDisposition.PENDING)
    try:
        yield ContextDelivery()
    finally:
        _context_disposition.reset(token)


@dataclass
class ContextHandoff:
    """Mark consumption immediately before entering framework execution."""

    consumed: bool = False

    def consume(self) -> None:
        self.consumed = True
        if _context_disposition.get() is not None:
            _context_disposition.set(ContextDisposition.CONSUMED)


@contextmanager
def context_handoff() -> Iterator[ContextHandoff]:
    """Retain context when setup exits abnormally, preserving the exception.

    After consumption, failure or cancellation cannot prove that execution
    did not receive the context, so commitment remains conservative.
    """
    handoff = ContextHandoff()
    try:
        yield handoff
    except BaseException:
        if (
            not handoff.consumed
            and _context_disposition.get() is ContextDisposition.PENDING
        ):
            _context_disposition.set(ContextDisposition.RETAIN)
        raise
