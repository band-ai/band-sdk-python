"""OnCleanup behavior for Pydantic AI."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_room_history(self):
        """Should remove room history on cleanup."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        # Add some history
        adapter._message_history["room-123"] = [
            ModelRequest(parts=[UserPromptPart(content="test")])
        ]
        assert "room-123" in adapter._message_history

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._message_history
