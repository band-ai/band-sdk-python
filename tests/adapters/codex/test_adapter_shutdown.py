"""Codex adapter: shutdown seals admission at once, owns its one cleanup, and
stays closed until an explicit restart confirms it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass

import pytest
import pytest_asyncio

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.exceptions import CleanupIncompleteError, InputNotAccepted
from band.core.retention import RoomRetentionConfig
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import (
    CountingCloseClient,
    HeldTurnClient,
    patch_codex_clients_by_room,
    send_bootstrap,
    start_room_process,
)
from tests.adapters.codexturns import turn_completed

HELD_ROOM = "room-a"
NEW_ROOM = "room-b"
TRANSITION_TIMEOUT_S = 1.0


@dataclass
class TwoRooms:
    """A started adapter whose held room has a turn parked in turn/start."""

    adapter: CodexAdapter
    held: HeldTurnClient
    new: CountingCloseClient
    held_turn: asyncio.Task[None]

    async def finish(self) -> None:
        self.held.finish_turn.set()
        self.held.finish_close.set()
        await asyncio.gather(self.held_turn, return_exceptions=True)


@pytest_asyncio.fixture(loop_scope="function")
async def two_rooms() -> AsyncIterator[TwoRooms]:
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


async def refused(attempt: Awaitable[None]) -> bool:
    try:
        await attempt
    except InputNotAccepted:
        return True
    return False


class TestAdapterShutdown:
    async def test_shutdown_seals_every_room_before_settling_held_work(
        self, two_rooms: TwoRooms
    ) -> None:
        stopping = asyncio.create_task(two_rooms.adapter.cleanup_all())
        await asyncio.sleep(0)

        assert await refused(send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM))
        assert await refused(start_room_process(two_rooms.adapter, HELD_ROOM))
        assert two_rooms.new.starts == 0
        await two_rooms.finish()
        await stopping
        assert (two_rooms.held.starts, two_rooms.held.closes) == (1, 1)

    async def test_successful_shutdown_refuses_turns_until_restarted(
        self, two_rooms: TwoRooms
    ) -> None:
        await two_rooms.finish()
        await two_rooms.adapter.cleanup_all()

        assert await refused(send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM))
        assert await refused(start_room_process(two_rooms.adapter, HELD_ROOM))
        await two_rooms.adapter.on_started("Codex Agent", "Restarted")
        await send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM)
        assert two_rooms.new.params_of(CodexRequestMethod.TURN_START)

    @pytest.mark.looptime
    async def test_settlement_counts_against_the_one_shutdown_deadline(
        self, two_rooms: TwoRooms
    ) -> None:
        with pytest.raises(CleanupIncompleteError):
            await two_rooms.adapter.cleanup_all()
        with pytest.raises(CleanupIncompleteError):
            await two_rooms.adapter.on_started("Codex Agent", "Too early")

        assert await refused(send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM))
        assert (two_rooms.held.starts, two_rooms.held.closes) == (1, 0)

    @pytest.mark.looptime
    async def test_concurrent_and_retried_callers_join_one_close(
        self, two_rooms: TwoRooms
    ) -> None:
        two_rooms.held.finish_turn.set()
        with pytest.raises(CleanupIncompleteError):
            await two_rooms.adapter.cleanup_all()
        waiters = [
            asyncio.create_task(two_rooms.adapter.cleanup_all()) for _ in range(2)
        ]
        await asyncio.sleep(0)
        two_rooms.held.finish_close.set()

        await asyncio.gather(*waiters)
        assert two_rooms.held.closes == 1

    async def test_cancelled_caller_leaves_the_shutdown_owned(
        self, two_rooms: TwoRooms
    ) -> None:
        two_rooms.held.finish_turn.set()
        stopping = asyncio.create_task(two_rooms.adapter.cleanup_all())
        await two_rooms.held.closing.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping

        assert await refused(send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM))
        two_rooms.held.finish_close.set()
        await two_rooms.adapter.on_started("Codex Agent", "Restarted")
        assert two_rooms.held.closes == 1

    async def test_failed_close_keeps_the_process_until_a_retry_closes_it(
        self, two_rooms: TwoRooms
    ) -> None:
        await two_rooms.finish()
        two_rooms.held.refuse_close = True
        with pytest.raises(ExceptionGroup):
            await two_rooms.adapter.cleanup_all()
        with pytest.raises(ExceptionGroup):
            await two_rooms.adapter.on_started("Codex Agent", "Close still fails")
        assert await refused(send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM))
        assert (two_rooms.held.starts, two_rooms.held.closes) == (1, 2)

        two_rooms.held.refuse_close = False
        await two_rooms.adapter.on_started("Codex Agent", "Restarted")
        assert (two_rooms.held.starts, two_rooms.held.closes) == (1, 3)
        await send_bootstrap(two_rooms.adapter, room_id=HELD_ROOM)
        assert two_rooms.held.starts == 2

    async def test_a_leaving_room_does_not_hold_up_another_room(
        self, two_rooms: TwoRooms
    ) -> None:
        two_rooms.held.finish_turn.set()
        await two_rooms.held_turn
        leaving = asyncio.create_task(two_rooms.adapter.on_cleanup(HELD_ROOM))
        await two_rooms.held.closing.wait()

        await send_bootstrap(two_rooms.adapter, room_id=NEW_ROOM)
        assert two_rooms.new.params_of(CodexRequestMethod.TURN_START)
        assert not leaving.done()
        two_rooms.held.finish_close.set()
        await leaving
