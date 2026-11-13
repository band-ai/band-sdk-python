"""Lexical ownership for a test's background operation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import AbstractContextManager, asynccontextmanager
from typing import Any, TypeVar

Result = TypeVar("Result")

# asyncio/windows_events.py: the proactor serves each accept as its own task.
PROACTOR_ACCEPT = "IocpProactor.accept.<locals>.accept_coro"


@asynccontextmanager
async def background(
    operation: Coroutine[Any, Any, Result] | asyncio.Task[Result],
) -> AsyncIterator[asyncio.Task[Result]]:
    """Cancel and join on every exit; the caller observes the operation's result."""
    task = asyncio.ensure_future(operation)
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def off_loop(manager: AbstractContextManager[Result]) -> AsyncIterator[Result]:
    """Enter and exit a blocking context manager in a worker thread.

    A minutes-long setup (an image build, a microVM boot) run on the loop
    thread starves every live connection on it, such as a session WebSocket.
    """
    value = await asyncio.to_thread(manager.__enter__)
    try:
        yield value
    except BaseException as error:
        if not await asyncio.to_thread(
            manager.__exit__, type(error), error, error.__traceback__
        ):
            raise
    else:
        await asyncio.to_thread(manager.__exit__, None, None, None)


def tasks_started_since(before: set[asyncio.Task[Any]]) -> set[asyncio.Task[Any]]:
    """Tasks started after ``before``, except a live server's accept.

    Windows' proactor loop replaces that accept task after every connection.
    """
    return {
        task
        for task in asyncio.all_tasks() - before
        if task.get_coro().__qualname__ != PROACTOR_ACCEPT
    }
