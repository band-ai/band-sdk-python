from __future__ import annotations

import pytest

from band.runtime.tools import AgentTools
from tests.identifiers import INVALID_IDS, UUID_ID
from tests.runtime.helpers import memory_client


class TestMemoryDispatch:
    @pytest.mark.parametrize(
        "name", ["band_get_memory", "band_supersede_memory", "band_archive_memory"]
    )
    @pytest.mark.parametrize("memory_id", INVALID_IDS)
    @pytest.mark.asyncio
    async def test_memory_validation_prevents_http(
        self, name: str, memory_id: str
    ) -> None:
        async with memory_client() as (rest, requests):
            outcome = await AgentTools("room-1", rest).execute_tool_call_structured(
                name, {"memory_id": memory_id}
            )
            assert not outcome.ok
            assert f"Invalid arguments for {name}" in outcome.error_message
            assert "memory_id:" in outcome.error_message
            assert requests == []

    @pytest.mark.parametrize(
        "name,method,suffix",
        [
            ("band_get_memory", "GET", ""),
            ("band_supersede_memory", "POST", "/supersede"),
            ("band_archive_memory", "POST", "/archive"),
        ],
    )
    @pytest.mark.asyncio
    async def test_valid_memory_dispatch_addresses_item(
        self, name: str, method: str, suffix: str
    ) -> None:
        async with memory_client() as (rest, requests):
            outcome = await AgentTools("room-1", rest).execute_tool_call_structured(
                name, {"memory_id": UUID_ID}
            )
            assert outcome.ok, outcome.error_message
            assert outcome.value["id"] == UUID_ID
            assert outcome.value["content"] == "remember this"
            assert [(q.method, q.url.path) for q in requests] == [
                (method, f"/api/v1/agent/memories/{UUID_ID}{suffix}")
            ]
