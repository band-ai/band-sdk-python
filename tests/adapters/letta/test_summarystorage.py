from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    RoomContext,
)
from band.converters.letta import LettaSessionState
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    make_platform_message,
)


class TestSummaryStorage:
    @pytest.mark.asyncio
    async def test_summary_stored_after_turn(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("The weather is sunny. More details follow.")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        room_ctx = adapter._rooms["room-1"]
        assert room_ctx.summary == "The weather is sunny. More details follow."
        assert room_ctx.last_interaction is not None
