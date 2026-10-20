from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    RoomContext,
)


class TestLettaAdapterOnCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_room_state(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._rooms
        # Memory consolidation was attempted
        mock_client.agents.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client

        # Room doesn't exist
        await adapter.on_cleanup("nonexistent")
        mock_client.agents.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_twice(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")  # Should not raise

        assert "room-1" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_cleanup_multi_room(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")
        adapter._rooms["room-2"] = RoomContext(agent_id="agent-2")

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._rooms
        assert "room-2" in adapter._rooms

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self) -> None:
        adapter = LettaAdapter()
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        # No client — should not raise
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._rooms

    @pytest.mark.asyncio
    async def test_cleanup_before_started(self) -> None:
        adapter = LettaAdapter()
        # Completely uninitialized — no client, no rooms
        await adapter.on_cleanup("room-1")
        assert "room-1" not in adapter._rooms
