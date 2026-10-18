from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class TestRejoinContext:
    @pytest.mark.asyncio
    async def test_rejoin_injects_time_away(self) -> None:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"

        last_time = datetime.now(UTC) - timedelta(hours=2)
        adapter._rooms["room-1"] = RoomContext(
            agent_id="agent-1",
            last_interaction=last_time,
            summary="Discussed project plan",
        )

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'm back!")
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
            is_session_bootstrap=True,
            room_id="room-1",
        )

        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        content = call_kwargs["messages"][0]["content"]
        assert "rejoined" in content
        assert "2h" in content
        assert "Discussed project plan" in content
