"""Native SDK execution through each adapter's public turn boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

from crewai.flow.flow import Flow, start

from band.adapters.crewai import CrewAIAdapter
from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
    WaitingDecision,
    get_current_flow_runtime,
)
from tests.adapters.crewaiworker.kit import NativeWork, ReadResource, native_agent
from tests.adapters.crewaiworker.peer import resource_peer
from tests.adapters.waiting import UNTIL_TIMEOUT_S
from tests.framework_conformance.turnprobes import turn_input, turn_tools, user_message

AdapterKind = Literal["crewai", "flow"]


class ResourceFlow(Flow):
    def __init__(self, work: NativeWork) -> None:
        super().__init__()
        self.work = work

    @start()
    async def read(self) -> WaitingDecision:
        runtime = get_current_flow_runtime()
        if runtime is None:
            raise RuntimeError("Flow runtime is unavailable")
        agent = native_agent(self.work, runtime.create_crewai_tools())
        await agent.kickoff_async("Read resource 1")
        return WaitingDecision(decision="waiting", reason="Resource read")


@dataclass(frozen=True)
class AdapterCase:
    adapter: CrewAIAdapter | CrewAIFlowAdapter
    work: NativeWork
    consumer: asyncio.Task[None]
    expected: dict[str, str]


@asynccontextmanager
async def adapter_case(kind: AdapterKind) -> AsyncIterator[AdapterCase]:
    async with resource_peer() as (client, expected):
        work = NativeWork(client, asyncio.get_running_loop())
        match kind:
            case "crewai":
                adapter = CrewAIAdapter(additional_tools=[(ReadResource, work.read)])
                # Inject the scripted model without constructing a credentialed LLM.
                adapter._tool_loop = work.loop
                adapter._crewai_agent = native_agent(
                    work, adapter.create_crewai_tools()
                )
            case "flow":
                adapter = CrewAIFlowAdapter(
                    CrewAIFlowAdapterConfig(metadata_namespace=uuid4().hex),
                    flow_factory=lambda: ResourceFlow(work),
                    state_source=HistoryCrewAIFlowStateSource(
                        acknowledge_test_only=True
                    ),
                    additional_tools=[(ReadResource, work.read)],
                )
                await adapter.on_started(uuid4().hex, "Reads a resource")
        tools = turn_tools()
        message = replace(user_message(), id=uuid4().hex, room_id=uuid4().hex)
        consumer = asyncio.create_task(adapter.on_event(turn_input(tools, message)))
        try:
            async with asyncio.timeout(UNTIL_TIMEOUT_S):
                await work.entered.wait()
            yield AdapterCase(adapter, work, consumer, expected)
        finally:
            work.release.set()
            work.native_release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if work.native_entered.is_set():
                await work.native_finished.wait()
            await adapter.cleanup_all()
