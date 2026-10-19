"""Codex adapter: /status reports the room's state without starting a turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.integrations.codex.types import (
    CodexRequestMethod,
)
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    ToolSchemaFakeTools,
    residency_of,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    LISTED_MODEL,
    FakeCodexClient,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_status_command_returns_state_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(
            adapter, content="@thenvoi/ar-2-darter /status", tools=tools
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.TURN_START not in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert len(tools.messages_sent) == 1
        assert "Codex status:" in tools.messages_sent[0]["content"]
        assert "thread_id: not mapped" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_status_reports_the_room_model_without_registering_a_room(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY), turn_completed()
        )

        await room.send("/status")
        assert residency_of(room.adapter, ROOM_ID) is None
        await room.send("work")
        await room.send("/status")

        assert f"selected_model: {LISTED_MODEL}\n" in room.chat[-1]
