"""Codex adapter: shutdown seals admission at once, owns its one cleanup, and
stays closed until an explicit restart confirms it."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import (
    HELD_ROOM,
    NEW_ROOM,
    TwoRooms,
    refused,
    send_bootstrap,
    start_room_process,
)


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
