"""Pytest fixtures shared by the Codex batch suites."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex import RpcEvent
from tests.adapters.codexbatch.kit import ROOM_ID, CodexBatchRoom, OpenRoom
from tests.adapters.codexturns import FakeCodexClient, make_codex_adapter


# Adapters run their turns on the loop that started them, so setup, the test
# and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def codex_room() -> AsyncIterator[OpenRoom]:
    """Open a room on a started manual-approval Codex adapter whose server plays
    ``events``; approvals wait an hour, so a regression that waits on the human
    hangs instead of passing by luck. Every room is cleaned up at the end."""
    rooms: list[CodexBatchRoom] = []

    async def open_room(*events: RpcEvent, **config: Any) -> CodexBatchRoom:
        client = FakeCodexClient(events=list(events))
        settings = {"approval_mode": "manual", "approval_wait_timeout_s": 3600}
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(**{**settings, **config})
        )
        await adapter.on_started("Agent", "A coding agent")
        rooms.append(room := CodexBatchRoom(adapter, client))
        return room

    yield open_room
    for room in rooms:
        await room.adapter.on_cleanup(ROOM_ID)
