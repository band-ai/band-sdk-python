"""Owned cleanup tasks: run so a failure is never lost, and report their
result without cancelling them."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Coroutine, Iterator
from contextlib import contextmanager
from inspect import iscoroutine
from typing import Any, TypeVar, cast

from band.core.exceptions import CleanupIncompleteError
from band.core.retention.config import RoomRetentionConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Callers awaiting each owned task that re-raise its failure themselves.
_reporters: Counter[asyncio.Task[Any]] = Counter()
# Finished tasks whose failure a caller re-raised or this module logged.
_reported: weakref.WeakSet[asyncio.Task[Any]] = weakref.WeakSet()
_labels: weakref.WeakKeyDictionary[asyncio.Task[Any], str] = weakref.WeakKeyDictionary()
# Strong refs so an abandoned owned gate is not collected before it settles.
_held: set[asyncio.Task[Any]] = set()


def spawn_owned(operation: Coroutine[Any, Any, T], *, label: str) -> asyncio.Task[T]:
    """Run work that outlives a cancelled caller; log its failure only when
    no caller is waiting to report it."""
    task: asyncio.Task[T] = asyncio.create_task(operation)
    _labels[task] = label
    _held.add(task)
    task.add_done_callback(_held.discard)
    task.add_done_callback(_log_unreported)
    return task


def _own_existing(task: asyncio.Task[T], *, label: str) -> asyncio.Task[T]:
    """Keep an already-scheduled task alive under owned-abandonment rules."""
    _labels.setdefault(task, label)
    _held.add(task)
    task.add_done_callback(_held.discard)
    task.add_done_callback(_log_unreported)
    return task


def rejoin_owned(
    current: asyncio.Task[None] | None,
    operation: Callable[[], Coroutine[object, object, None]],
    *,
    label: str,
) -> asyncio.Task[None]:
    """Join the running or settled owned task; start a new one only after it
    failed, so concurrent callers share one attempt and a retry follows a
    settled failure."""
    if current is None or (current.done() and task_failure(current) is not None):
        return spawn_owned(operation(), label=label)
    return current


def _log_unreported(task: asyncio.Task[Any]) -> None:
    if task in _reporters or task in _reported or task.cancelled():
        return
    if (label := _labels.get(task)) is None:
        return
    _reported.add(task)
    if (error := task.exception()) is not None:
        logger.warning("%s failed: %s", label, error, exc_info=error)


def task_failure(task: asyncio.Task[Any]) -> BaseException | None:
    """A finished owned cleanup's failure; cancellation is incomplete cleanup."""
    if task.cancelled():
        return CleanupIncompleteError("Cleanup was cancelled")
    return task.exception()


@contextmanager
def reporting(tasks: Collection[asyncio.Task[Any]]) -> Iterator[None]:
    """Mark the caller as the reporter of ``tasks``' failures while it waits.

    A caller cancelled while waiting reports nothing, so a failure it never
    saw is logged here when the done callback already ran.
    """
    _reporters.update(tasks)
    cancelled = False
    try:
        yield
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        _reporters.subtract(tasks)
        for task in tasks:
            if not _reporters[task]:
                del _reporters[task]
            if not task.done():
                continue
            if cancelled:
                _log_unreported(task)
            else:
                _reported.add(task)


async def await_owned(
    tasks: Collection[asyncio.Task[Any]], timeout: float | None
) -> bool:
    """Wait up to ``timeout`` for owned ``tasks`` without cancelling them, as
    the caller that reports their failures; whether all finished."""
    with reporting(tasks):
        done, _ = await asyncio.wait(tasks, timeout=timeout)
    return len(done) == len(tasks)


async def await_owned_result(
    operation: Awaitable[T],
    *,
    timeout: float | None,
    label: str,
) -> T:
    """Await a provider gate under owned-abandonment rules.

    Expiry stops the caller wait without cancelling ``operation``. Ownership
    keeps the task alive for cleanup or late-identity observation; a deadline
    never proves the gate settled.
    """
    if isinstance(operation, asyncio.Task):
        task = _own_existing(operation, label=label)
    elif iscoroutine(operation):
        task = spawn_owned(cast("Coroutine[Any, Any, T]", operation), label=label)
    else:
        # Futures and other non-coroutine awaitables cannot be passed to
        # create_task; observe them from an owned task that outlives this wait.
        async def observe() -> T:
            return await operation

        task = spawn_owned(observe(), label=label)
    if await await_owned({task}, timeout):
        return task.result()
    raise TimeoutError(f"{label} exceeded its budget")


async def report_owned(task: asyncio.Task[Any], timeout: float | None) -> None:
    """Report an owned cleanup's result within ``timeout`` without cancelling it."""
    if not await await_owned({task}, timeout):
        raise CleanupIncompleteError(f"Cleanup still running after {timeout}s")
    if (error := task_failure(task)) is not None:
        raise error


def cleanup_deadline(config: RoomRetentionConfig) -> float:
    """The one deadline an adapter's cleanup shares across rooms and MCP."""
    return asyncio.get_running_loop().time() + config.transition_timeout_s


async def finish_by(deadline: float, cleanup: Awaitable[None], *, what: str) -> None:
    """Await ``cleanup`` until ``deadline``; an unfinished one stays with its
    owner for a retry to join. A timeout ``cleanup`` raised itself propagates."""
    budget = asyncio.timeout_at(deadline)
    try:
        async with budget:
            await cleanup
    except TimeoutError as error:
        if not budget.expired():
            raise
        raise CleanupIncompleteError(
            f"{what} still running after its transition budget"
        ) from error
