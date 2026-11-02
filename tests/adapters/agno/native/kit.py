"""Gated real synchronous native work and released Agno execution."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agno.models.message import Message
from agno.tools.function import Function, FunctionCall

from band.integrations.agno.activity import observing_run
from band.integrations.agno.entrypoints import observe_function
from band.runtime.tools.agent import AgentTools
from tests.adapters.agno.helpers import CapturingModel

NATIVE_RESULT = "native-result"
NATIVE_NAME = "lookup"


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
            return NATIVE_RESULT
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


async def run_model_call(
    tools: AgentTools, function: Function, arguments: dict[str, Any] | None = None
) -> tuple[FunctionCall, list[Message]]:
    """Consume released Agno execution, including its generator results."""
    call = FunctionCall(function=function, arguments=arguments or {}, call_id="native")
    results: list[Message] = []
    async with observing_run(tools):
        async for _ in CapturingModel().arun_function_calls([call], results):
            pass
    return call, results
