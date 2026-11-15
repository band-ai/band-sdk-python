"""Validate memory arguments before the real CrewAI wrapper reaches REST."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from crewai.tools import ToolFailure

from band.core.types import AdapterFeatures, Capability
from band.integrations.crewai.tools import (
    CrewAIToolContext,
    NoopReporter,
    build_band_crewai_tools,
)
from band.runtime.tools import AgentTools
from tests.crewaicontract import tool_result_data
from tests.identifiers import INVALID_IDS, UUID_ID
from tests.runtime.helpers import memory_client


class TestRealMemoryValidation:
    @pytest.mark.parametrize(
        "args", [{}, *({"memory_id": value} for value in INVALID_IDS)]
    )
    async def test_invalid_arguments_fail_before_rest(
        self, args: dict[str, Any]
    ) -> None:
        async with memory_client() as (rest, requests):
            context = CrewAIToolContext(
                room_id="room-1", tools=AgentTools("room-1", rest)
            )
            built = build_band_crewai_tools(
                get_context=lambda: context,
                reporter=NoopReporter(),
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )
            tool = next(t for t in built if t.name == "band_get_memory")

            result = await asyncio.to_thread(tool._run, **args)

            assert isinstance(result, ToolFailure)
            data = tool_result_data(result)
            assert data["status"] == "error"
            assert "memory_id" in data["message"]
            assert requests == []

    async def test_valid_arguments_reach_the_memory_endpoint(self) -> None:
        async with memory_client() as (rest, requests):
            context = CrewAIToolContext(
                room_id="room-1", tools=AgentTools("room-1", rest)
            )
            built = build_band_crewai_tools(
                get_context=lambda: context,
                reporter=NoopReporter(),
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )
            tool = next(t for t in built if t.name == "band_get_memory")

            result = tool_result_data(
                await asyncio.to_thread(tool._run, memory_id=UUID_ID)
            )

            assert result["status"] == "success"
            assert result["id"] == UUID_ID
            assert [q.url.path for q in requests] == [
                f"/api/v1/agent/memories/{UUID_ID}"
            ]
