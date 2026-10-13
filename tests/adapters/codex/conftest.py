"""Fixtures for the Codex adapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codex.kit import (  # noqa: F401
    CleaningTurn,
    CleaningTurnClient,
    CodexApprovalRoom,
    CodexRoom,
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


@pytest_asyncio.fixture(loop_scope="function")
async def cleaning_turn() -> AsyncIterator[CleaningTurn]:
    """A room whose turn was cancelled mid turn/start and is still cleaning up;
    the cleanup finishes at teardown at the latest."""
    client = CleaningTurnClient()
    room = CodexRoom(make_codex_adapter(client), client)
    sending = asyncio.create_task(room.send("work"))
    await client.started.wait()
    sending.cancel()
    await client.cleaning.wait()
    try:
        yield CleaningTurn(room=room, client=client)
    finally:
        client.release.set()
        with pytest.raises(asyncio.CancelledError):
            await sending
        await room.adapter.cleanup_all()
