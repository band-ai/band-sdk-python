"""A customer Flow keeps a runtime reference after its turn has ended."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from band.adapters.crewai_flow import (
    CrewAIFlowRuntimeTools,
    WaitingDecision,
    get_current_flow_runtime,
)
from band.runtime.custom_tools import get_custom_tool_name
from tests.adapters.crewaiworker.kit import ReadResource


class SavedFlow:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.runtime: CrewAIFlowRuntimeTools | None = None
        self.subtools: list[Any] = []
        self.calls = 0
        self.result: dict[str, str] | None = None

    async def read(self, args: ReadResource) -> str:
        self.calls += 1
        response = await self.client.get("/resource")
        response.raise_for_status()
        self.result = response.json()
        return response.text

    async def kickoff_async(self, inputs: dict[str, Any]) -> WaitingDecision:
        self.runtime = get_current_flow_runtime()
        if self.runtime is None:
            raise RuntimeError("Flow runtime is unavailable")
        self.subtools = self.runtime.create_crewai_tools()
        await self.runtime.call_tool(get_custom_tool_name(ReadResource), resource_id=1)
        return WaitingDecision(decision="waiting", reason="Resource read")


async def call_saved_runtime(flow: SavedFlow) -> Any:
    if flow.runtime is None:
        raise RuntimeError("Flow has not run")
    return await flow.runtime.call_tool(
        get_custom_tool_name(ReadResource), resource_id=1
    )


async def call_saved_subcrew_tool(flow: SavedFlow) -> Any:
    tool = next(
        tool
        for tool in flow.subtools
        if tool.name == get_custom_tool_name(ReadResource)
    )
    return await asyncio.to_thread(tool.run, resource_id=1)
