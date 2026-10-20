from __future__ import annotations

import pytest


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_cleans_up_room_history(self, CrewAIAdapter, mock_crewai_agent):
        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        adapter._message_history["room-123"] = [{"role": "user", "content": "test"}]
        assert "room-123" in adapter._message_history

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._message_history
