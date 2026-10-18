from __future__ import annotations

import pytest

from band.runtime.tools import (
    AgentTools,
)
from tests.identifiers import UUID_ID
from tests.runtime.helpers import memory_client


class TestValidMemoryDispatchAddressesItem:
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
