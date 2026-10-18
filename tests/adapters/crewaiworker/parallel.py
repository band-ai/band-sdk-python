"""Real SDK parallel branches sharing an HTTP resource."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import uuid4

import httpx
from crewai.flow.flow import Flow, listen, start

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
    WaitingDecision,
    get_current_flow_runtime,
)
from band.runtime.custom_tools import get_custom_tool_name
from tests.adapters.crewaiworker.kit import ReadResource
from tests.adapters.crewaiworker.peer import resource_peer
from tests.framework_conformance.turnprobes import turn_input, turn_tools, user_message

BranchKind = Literal["starts", "listeners"]


@dataclass
class ParallelWork:
    client: httpx.AsyncClient
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    fail: asyncio.Event = field(default_factory=asyncio.Event)
    tool_finished: asyncio.Event = field(default_factory=asyncio.Event)
    sibling_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    replies: list[dict[str, str]] = field(default_factory=list)

    async def read(self, args: ReadResource) -> str:
        self.entered.set()
        try:
            await self.fail.wait()
            raise RuntimeError("Resource read failed")
        finally:
            self.tool_finished.set()


class ParallelFlow(Flow):
    def __init__(self, work: ParallelWork) -> None:
        super().__init__()
        self.work = work

    async def read_tool(self) -> WaitingDecision:
        runtime = get_current_flow_runtime()
        if runtime is None:
            raise RuntimeError("Flow runtime is unavailable")
        await runtime.call_tool(get_custom_tool_name(ReadResource), resource_id=1)
        return WaitingDecision(decision="waiting", reason="Resource read")

    async def read_sibling(self) -> WaitingDecision:
        self.work.sibling_entered.set()
        await self.work.release.wait()
        response = await self.work.client.get("/resource")
        response.raise_for_status()
        self.work.replies.append(response.json())
        return WaitingDecision(decision="waiting", reason="Sibling read")


class ParallelStarts(ParallelFlow):
    @start()
    async def tool(self) -> WaitingDecision:
        return await self.read_tool()

    @start()
    async def sibling(self) -> WaitingDecision:
        return await self.read_sibling()


class ParallelListeners(ParallelFlow):
    @start()
    async def begin(self) -> None:
        return None

    @listen(begin)
    async def tool(self) -> WaitingDecision:
        return await self.read_tool()

    @listen(begin)
    async def sibling(self) -> WaitingDecision:
        return await self.read_sibling()


class UnrelatedFlow(Flow):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    @start()
    async def wait(self) -> None:
        self.entered.set()
        await self.release.wait()


@dataclass(frozen=True)
class ParallelCase:
    adapter: CrewAIFlowAdapter
    work: ParallelWork
    consumer: asyncio.Task[None]
    expected: dict[str, str]


@asynccontextmanager
async def parallel_case(kind: BranchKind) -> AsyncIterator[ParallelCase]:
    async with resource_peer() as (client, expected):
        work = ParallelWork(client)
        flow_type = ParallelStarts if kind == "starts" else ParallelListeners
        adapter = CrewAIFlowAdapter(
            CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
            flow_factory=lambda: flow_type(work),
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
            additional_tools=[(ReadResource, work.read)],
        )
        await adapter.on_started(uuid4().hex, "Parallel Flow")
        message = replace(user_message(), id=uuid4().hex, room_id=uuid4().hex)
        consumer = asyncio.create_task(
            adapter.on_event(turn_input(turn_tools(), message))
        )
        try:
            await work.entered.wait()
            await work.sibling_entered.wait()
            yield ParallelCase(adapter, work, consumer, expected)
        finally:
            work.release.set()
            work.fail.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await adapter.cleanup_all()
