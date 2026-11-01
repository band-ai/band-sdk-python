"""Native callable bodies with observable execution and finally boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
from contextlib import contextmanager

from band.core.protocols import AgentToolsProtocol
from band.integrations.agno.context import current_tools
from tests.adapters.agno.native.kit import NATIVE_RESULT

BODY_FAILURE = "Native body failed"


def lookup() -> str:
    return "sync fallback must not execute"


class NativeBodyError(RuntimeError):
    """Fail actual native execution after admission."""


class NativeBody:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self._tools: AgentToolsProtocol | None = None

    @property
    def tools(self) -> AgentToolsProtocol:
        if self._tools is None:
            raise AssertionError("The native body has not entered")
        return self._tools

    @contextmanager
    def executing(self) -> Iterator[None]:
        self._tools = current_tools.get()
        self.entered.set()
        try:
            yield
        finally:
            self.finished.set()

    async def coroutine(self) -> str:
        with self.executing():
            await self.release.wait()
            return NATIVE_RESULT

    def generator(self) -> Iterator[str]:
        with self.executing():
            yield NATIVE_RESULT

    def counted_generator(self, count: int) -> Iterator[str]:
        with self.executing():
            yield str(count)

    async def async_generator(self) -> AsyncIterator[str]:
        with self.executing():
            yield NATIVE_RESULT

    def failing_function(self) -> str:
        with self.executing():
            raise NativeBodyError(BODY_FAILURE)

    async def failing_coroutine(self) -> str:
        with self.executing():
            raise NativeBodyError(BODY_FAILURE)

    def failing_generator(self) -> Iterator[str]:
        with self.executing():
            yield NATIVE_RESULT
            raise NativeBodyError(BODY_FAILURE)

    async def failing_async_generator(self) -> AsyncIterator[str]:
        with self.executing():
            yield NATIVE_RESULT
            raise NativeBodyError(BODY_FAILURE)


async def advance_generator(iterator: Iterator[str]) -> str:
    return next(iterator)


async def close_generator(iterator: Generator[str, None, None]) -> None:
    iterator.close()


async def advance_async_generator(iterator: AsyncIterator[str]) -> str:
    return await anext(iterator)


async def close_async_generator(iterator: AsyncGenerator[str, None]) -> None:
    await iterator.aclose()
