"""Reporting an owned cleanup task's result without cancelling it."""

from __future__ import annotations

import asyncio
from typing import Any

from band.core.exceptions import CleanupIncompleteError


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
