from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    RoomContext,
)


class TestDeleteAgentsOnCleanup:
    @pytest.mark.asyncio
    async def test_opt_in_deletes_agent_instead_of_consolidating(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(delete_agents_on_cleanup=True))
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.delete.assert_awaited_once_with("agent-1")
        # No consolidation prompt when the agent is being deleted
        mock_client.agents.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_keeps_agent(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_can_skip_consolidation_on_cleanup(self) -> None:
        adapter = LettaAdapter(
            config=LettaAdapterConfig(consolidate_memory_on_cleanup=False)
        )
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        mock_client.agents.delete.assert_not_called()
        mock_client.agents.messages.create.assert_not_called()
        assert "room-1" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_delete_failure_does_not_propagate(self) -> None:
        adapter = LettaAdapter(config=LettaAdapterConfig(delete_agents_on_cleanup=True))
        mock_client = AsyncMock()
        adapter._client = mock_client
        mock_client.agents.delete.side_effect = Exception("gone already")
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")  # should not raise
        assert "room-1" not in adapter._rooms
