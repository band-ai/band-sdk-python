from __future__ import annotations

import pytest

from band.runtime.tools import (
    AgentTools,
)
from tests.identifiers import INVALID_IDS
from tests.runtime.helpers import memory_client


class TestMemoryValidationPreventsHttp:
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
