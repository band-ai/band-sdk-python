"""Lexical ownership for a test's background operation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

Result = TypeVar("Result")


@asynccontextmanager
async def background(
    operation: Coroutine[Any, Any, Result] | asyncio.Task[Result],
) -> AsyncIterator[asyncio.Task[Result]]:
    """Cancel and join on every exit; the caller observes the operation's result."""
    task = (
        operation
        if isinstance(operation, asyncio.Task)
        else asyncio.create_task(operation)
    )
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
