"""One Copilot session per room; same room reuses its session."""

from __future__ import annotations

import pytest

from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestPerRoomIsolation:
    @pytest.mark.asyncio
    async def test_two_rooms_get_two_sessions(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools_a, tools_b = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        await run_message(adapter, tools_a, room_id="room-a", content="for A")
        await run_message(adapter, tools_b, room_id="room-b", content="for B")

        assert len(client.sessions) == 2
        session_a, session_b = client.sessions
        assert session_a.session_id == "band-copilot-agent-room-a"
        assert session_b.session_id == "band-copilot-agent-room-b"
        # No prompt leakage across rooms.
        assert "for B" not in session_a.prompts[0]
        assert "for A" not in session_b.prompts[0]

    @pytest.mark.asyncio
    async def test_same_room_reuses_session(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)
        await run_message(adapter, tools, is_session_bootstrap=False)

        assert len(client.sessions) == 1
        assert len(client.sessions[0].prompts) == 2
