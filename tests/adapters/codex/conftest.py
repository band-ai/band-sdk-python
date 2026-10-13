"""Fixtures for the Codex adapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.retention import RoomRetentionConfig
from tests.adapters.codex.kit import (  # noqa: F401
    HELD_ROOM,
    NEW_ROOM,
    TRANSITION_TIMEOUT_S,
    CleaningTurn,
    CleaningTurnClient,
    CodexApprovalRoom,
    CodexRoom,
    CountingCloseClient,
    HeldTurnClient,
    TwoRooms,
    codex_room,
    patch_codex_clients_by_room,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    turn_completed,
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
    # A leave reports its unfinished cleanup within this bound.
    config = CodexAdapterConfig(
        retention=RoomRetentionConfig(transition_timeout_s=TRANSITION_TIMEOUT_S)
    )
    room = CodexRoom(make_codex_adapter(client, config=config), client)
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


@pytest_asyncio.fixture(loop_scope="function")
async def two_rooms() -> AsyncIterator[TwoRooms]:
    """A started adapter whose held room has a turn parked in turn/start."""
    held = HeldTurnClient(
        events=[turn_completed(), turn_completed("turn-2")], hold=True
    )
    new = CountingCloseClient(events=[turn_completed()])
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            client_close_timeout_s=None,
            turn_settle_timeout_s=30,
            retention=RoomRetentionConfig(transition_timeout_s=TRANSITION_TIMEOUT_S),
        )
    )
    patch_codex_clients_by_room(adapter, {HELD_ROOM: held, NEW_ROOM: new})
    await adapter.on_started("Codex Agent", "A coding agent")
    held_turn = asyncio.create_task(send_bootstrap(adapter, room_id=HELD_ROOM))
    await held.turn_started.wait()
    rooms = TwoRooms(adapter=adapter, held=held, new=new, held_turn=held_turn)
    yield rooms
    await rooms.finish()
    await adapter.cleanup_all()


@pytest_asyncio.fixture(loop_scope="function")
async def started_codex() -> AsyncIterator[
    Callable[[FakeCodexClient], Awaitable[CodexAdapter]]
]:
    """Start Codex adapters over fake app-servers; each is shut down at the end."""
    adapters: list[CodexAdapter] = []

    async def start(client: FakeCodexClient) -> CodexAdapter:
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        adapters.append(adapter)
        return adapter

    yield start
    for adapter in adapters:
        await adapter.cleanup_all()
