"""Fixtures for the Codex adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest_asyncio

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex import RpcEvent
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexApprovalRoom,
    CodexRoom,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


# Adapters run their turns on the loop that started them, so setup, the test
# and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def codex_room() -> AsyncIterator[Callable[..., Awaitable[CodexRoom]]]:
    """Open a room on a started manual-approval Codex adapter whose server
    plays ``events``; every room is cleaned up at the end."""
    rooms: list[CodexRoom] = []

    async def open_room(
        *events: RpcEvent, client: FakeCodexClient | None = None, **config: Any
    ) -> CodexRoom:
        client = client or FakeCodexClient(events=list(events))
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(**{"approval_mode": "manual", **config})
        )
        await adapter.on_started("Agent", "A coding agent")
        rooms.append(room := CodexRoom(adapter, client))
        return room

    yield open_room
    for room in rooms:
        await room.adapter.on_cleanup(ROOM_ID)


@pytest_asyncio.fixture(loop_scope="function")
async def approval_room() -> AsyncIterator[Callable[..., Awaitable[CodexApprovalRoom]]]:
    """Open a Codex room in manual approval mode; asks still waiting when the
    test ends are cancelled."""
    rooms: list[CodexApprovalRoom] = []

    async def open_room(**config: Any) -> CodexApprovalRoom:
        adapter = make_codex_adapter(
            FakeCodexClient(events=[]),
            config=CodexAdapterConfig(
                **{"approval_mode": "manual", "approval_wait_timeout_s": 5, **config}
            ),
        )
        await adapter.on_started("Agent", "A coding agent")
        rooms.append(room := CodexApprovalRoom(adapter))
        return room

    yield open_room
    for room in rooms:
        for ask in room.asks:
            ask.cancel()
