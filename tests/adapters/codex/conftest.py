"""Fixtures for the Codex adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest_asyncio

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codex.kit import (  # noqa: F401
    CodexApprovalRoom,
    codex_room,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


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
