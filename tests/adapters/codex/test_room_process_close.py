"""Codex adapter: leaving a room closes that room's process."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.integrations.codex.types import (
    CodexSessionState,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
    patch_codex_clients_by_room,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestRoomProcessClose:
    @pytest.mark.asyncio
    async def test_cleanup_closes_client_when_last_room_removed(self) -> None:
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(room_id=ROOM_ID),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=ROOM_ID,
        )

        assert fake_client.closed is False
        await adapter.on_cleanup(ROOM_ID)
        assert fake_client.closed is True

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self) -> None:
        """Calling on_cleanup twice for the same room should not raise."""
        fake_client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(room_id=ROOM_ID),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=ROOM_ID,
        )

        await adapter.on_cleanup(ROOM_ID)
        assert fake_client.closed is True
        # Second cleanup should not raise
        await adapter.on_cleanup(ROOM_ID)

    @pytest.mark.asyncio
    async def test_cleanup_multi_room_closes_each_room_client(self) -> None:
        """Each room owns its Codex client; cleanup closes only that room's client."""
        clients = {
            ROOM_ID: FakeCodexClient(events=[turn_completed()]),
            # Each room owns a separate FakeCodexClient with its own turn
            # counter, so room-2's first turn is also "turn-1" -- matching
            # this room's client, not a global counter.
            "room-2": FakeCodexClient(events=[turn_completed()]),
        }
        adapter = CodexAdapter(config=CodexAdapterConfig())
        patch_codex_clients_by_room(adapter, clients)
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        for room_id in (ROOM_ID, "room-2"):
            await deliver_message(
                adapter,
                make_platform_message(room_id=room_id),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=room_id,
            )

        await adapter.on_cleanup(ROOM_ID)
        assert clients[ROOM_ID].closed is True
        assert clients["room-2"].closed is False

        await adapter.on_cleanup("room-2")
        assert clients["room-2"].closed is True
