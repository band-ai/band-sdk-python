"""Owned cleanup tasks: run so a failure is never lost, and report their
result without cancelling them."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from functools import partial
from typing import Any

from band.core.exceptions import CleanupIncompleteError

logger = logging.getLogger(__name__)


def spawn_owned(
    operation: Coroutine[object, object, None], *, label: str
) -> asyncio.Task[None]:
    """Run work that outlives a cancelled caller; log its failure even when
    nobody is left to await it."""
    task = asyncio.create_task(operation)
    task.add_done_callback(partial(_log_failure, label=label))
    return task


def _log_failure(task: asyncio.Task[None], *, label: str) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.warning("%s failed: %s", label, error)


def task_failure(task: asyncio.Task[Any]) -> BaseException | None:
    """A finished owned cleanup's failure; cancellation is incomplete cleanup."""
    if task.cancelled():
        return CleanupIncompleteError("Cleanup was cancelled")
    return task.exception()


async def report_owned(task: asyncio.Task[Any], timeout: float) -> None:
    """Report an owned cleanup's result within ``timeout`` without cancelling it."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        raise CleanupIncompleteError(f"Cleanup still running after {timeout}s")
    if (error := task_failure(task)) is not None:
        raise error
