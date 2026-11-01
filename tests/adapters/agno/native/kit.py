"""Gated real synchronous native work and released Agno execution."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agno.tools.function import Function, FunctionCall

from band.integrations.agno.activity import observing_run
from band.integrations.agno.entrypoints import observe_function
from band.runtime.tools.agent import AgentTools
from tests.adapters.agno.helpers import CapturingModel


class SyncBody:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.finished = asyncio.Event()
        self.release = threading.Event()

    def lookup(self) -> str:
        self.loop.call_soon_threadsafe(self.entered.set)
        try:
            self.release.wait()
            return "native-result"
        finally:
            self.loop.call_soon_threadsafe(self.finished.set)


@asynccontextmanager
async def native_call(
    tools: AgentTools, function: Function
) -> AsyncIterator[asyncio.Task[Any]]:
    async def execute() -> Any:
        async with observing_run(tools):
            call = FunctionCall(
                function=observe_function(function), arguments={}, call_id="native"
            )
            return await CapturingModel().arun_function_call(call)

    task = asyncio.create_task(execute())
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
