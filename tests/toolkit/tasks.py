"""Lexical ownership for a test's background operation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import AbstractContextManager, asynccontextmanager
from typing import Any, TypeVar

Result = TypeVar("Result")

logger = logging.getLogger(__name__)

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
    Cancelling the caller cannot stop the thread, so a cancellation during
    the enter waits for it and exits whatever it entered before re-raising.
    The enter is a bare executor future, not a task, so the loop's shutdown
    cancels only the caller.
    """
    entering = asyncio.get_running_loop().run_in_executor(None, manager.__enter__)
    try:
        value = await asyncio.shield(entering)
    except asyncio.CancelledError:
        await asyncio.wait([entering])
        if error := entering.exception():
            logger.warning(
                "Enter failed after its caller was cancelled", exc_info=error
            )
        else:
            await asyncio.to_thread(manager.__exit__, None, None, None)
        raise
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
