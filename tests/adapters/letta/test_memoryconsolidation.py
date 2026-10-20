from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    RoomContext,
)


class TestMemoryConsolidation:
    @pytest.mark.asyncio
    async def test_consolidation_on_cleanup(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.messages.create.assert_called_once()
        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        assert "Consolidate" in call_kwargs["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_consolidation_failure_does_not_propagate(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.side_effect = Exception("API error")

        # Should not raise
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._rooms
