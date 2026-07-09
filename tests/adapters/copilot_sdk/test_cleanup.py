"""Room cleanup disconnects sessions; only cleanup_all stops the client."""

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


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_disconnects_room_but_keeps_client_alive(self):
        """The client serves the whole adapter lifetime; only cleanup_all stops it."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools, room_id="room-a")
        await run_message(adapter, tools, room_id="room-b")

        await adapter.on_cleanup("room-a")
        await adapter.on_cleanup("room-b")

        assert client.sessions[0].disconnected
        assert client.sessions[1].disconnected
        assert not client.stopped

        # A room joined after all others left must not pay a client restart.
        await run_message(adapter, tools, room_id="room-c")
        assert client.sessions[2].prompts

    @pytest.mark.asyncio
    async def test_cleanup_all_stops_client(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        await adapter.cleanup_all()

        assert client.sessions[0].disconnected
        assert client.stopped

    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_safe(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)

        await adapter.on_cleanup("never-seen-room")
