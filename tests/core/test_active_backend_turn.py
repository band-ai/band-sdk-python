"""Expose only the backend handle whose turn can still accept work."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from band.core.turn_lifecycle import BackendTurns


class TestActiveBackendTurn:
    async def test_the_handle_is_visible_only_during_its_turn(self) -> None:
        handle = object()
        turns = BackendTurns[object](AsyncMock(), provider="test")

        async with turns.running("room", handle):
            assert turns.active_handle("room") is handle

        assert turns.active_handle("room") is None

    async def test_a_turn_without_a_handle_becomes_visible_when_bound(self) -> None:
        handle = object()
        turns = BackendTurns[object](AsyncMock(), provider="test")

        async with turns.running("room"):
            assert turns.active_handle("room") is None
            turns.bind("room", handle)
            assert turns.active_handle("room") is handle

    async def test_requested_stop_hides_the_handle_before_native_stop_finishes(
        self,
    ) -> None:
        stopping = asyncio.Event()
        stopped = asyncio.Event()

        async def cancel(handle: object) -> None:
            del handle
            stopping.set()
            await stopped.wait()

        turns = BackendTurns[object](cancel, provider="test")
        try:
            async with turns.running("room", object()):
                turns.cancel("room")
                await stopping.wait()
                assert turns.active_handle("room") is None
        finally:
            stopped.set()
            await turns.drain()
