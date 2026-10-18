"""Released native CrewAI execution with a scripted model and real HTTP."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
from crewai import Agent, BaseLLM
from pydantic import BaseModel

from band.core.exceptions import InputNotAccepted
from band.integrations.crewai.reporting import CrewAIToolContext, NoopReporter
from band.integrations.crewai.runtime import run_async
from band.integrations.crewai.tools import build_band_crewai_tools
from band.integrations.crewai.turn import ToolTurn
from band.runtime.custom_tools import get_custom_tool_name
from tests.adapters.crewaiworker.peer import resource_peer
from tests.adapters.waiting import UNTIL_TIMEOUT_S
from tests.framework_conformance.turnprobes import turn_tools


class ReadResource(BaseModel):
    """Read the shared resource."""

    resource_id: int


@dataclass
class NativeWork:
    client: httpx.AsyncClient
    loop: asyncio.AbstractEventLoop
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    tool_settled: asyncio.Event = field(default_factory=asyncio.Event)
    native_entered: asyncio.Event = field(default_factory=asyncio.Event)
    native_finished: asyncio.Event = field(default_factory=asyncio.Event)
    native_release: threading.Event = field(default_factory=threading.Event)
    tool_calls: int = 0
    resource_open_at_settlement: bool = False
    native_reply: dict[str, str] | None = None
    late_error: InputNotAccepted | None = None

    async def read(self, args: ReadResource) -> str:
        self.tool_calls += 1
        response = await self.client.get("/resource")
        response.raise_for_status()
        self.entered.set()
        try:
            await self.release.wait()
            return response.text
        finally:
            self.resource_open_at_settlement = not self.client.is_closed
            self.tool_settled.set()


class NativeLLM(BaseLLM):
    def __init__(self, work: NativeWork) -> None:
        super().__init__(model="scripted-native-model")
        self.work = work
        self.late_tool: Any = None

    def supports_function_calling(self) -> bool:
        return True

    def call(self, messages: Any, **kwargs: Any) -> Any:
        if not any(message.get("role") == "tool" for message in messages):
            return [
                {
                    "id": "resource-read",
                    "type": "function",
                    "function": {
                        "name": get_custom_tool_name(ReadResource),
                        "arguments": '{"resource_id":1}',
                    },
                }
            ]
        self.work.loop.call_soon_threadsafe(self.work.native_entered.set)
        try:
            if not self.work.native_release.wait(UNTIL_TIMEOUT_S):
                raise TimeoutError("Native model work was not released")
            try:
                self.late_tool.run(resource_id=1)
            except InputNotAccepted as error:
                self.work.late_error = error
            response = run_async(
                self.work.client.get("/resource"), fallback_loop=self.work.loop
            )
            response.raise_for_status()
            self.work.native_reply = response.json()
            return "Finished"
        finally:
            self.work.loop.call_soon_threadsafe(self.work.native_finished.set)

    async def acall(self, messages: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.call, messages, **kwargs)


@dataclass(frozen=True)
class NativeCase:
    turn: ToolTurn
    client: httpx.AsyncClient
    work: NativeWork
    consumer: asyncio.Task[Any]
    native: asyncio.Task[Any]
    expected: dict[str, str]


def native_agent(work: NativeWork, tools: list[Any]) -> Agent:
    model = NativeLLM(work)
    model.late_tool = next(
        tool for tool in tools if tool.name == get_custom_tool_name(ReadResource)
    )
    return Agent(
        role=uuid4().hex,
        goal="Read the resource",
        backstory="Read the resource using the provided tool.",
        llm=model,
        tools=tools,
        verbose=False,
    )


@asynccontextmanager
async def native_case() -> AsyncIterator[NativeCase]:
    async with resource_peer() as (client, expected):
        loop = asyncio.get_running_loop()
        work = NativeWork(client, loop)
        tools = turn_tools()
        turn = ToolTurn(tools)
        context = CrewAIToolContext(uuid4().hex, tools, tool_turn=turn)
        native_tools = build_band_crewai_tools(
            get_context=lambda: context,
            reporter=NoopReporter(),
            custom_tools=[(ReadResource, work.read)],
            fallback_loop=loop,
        )
        agent = native_agent(work, native_tools)
        started: asyncio.Future[asyncio.Task[Any]] = loop.create_future()

        async def execute() -> Any:
            native = asyncio.current_task()
            assert native is not None
            started.set_result(native)
            return await agent.kickoff_async("Read resource 1")

        consumer = asyncio.create_task(turn.execute(execute))
        try:
            async with asyncio.timeout(UNTIL_TIMEOUT_S):
                await work.entered.wait()
                native = await started
            yield NativeCase(turn, client, work, consumer, native, expected)
        finally:
            work.release.set()
            work.native_release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if started.done() and not started.cancelled():
                await asyncio.gather(started.result(), return_exceptions=True)
            await turn.close(cancel=True)
