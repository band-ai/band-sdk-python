"""Interrupting a room while its Cursor session is still being set up."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.client.streaming import ControlMode
from tests.adapters.cursor_acp.kit import (
    CursorRoom,
    SetupGatedAgent,
)


class TestSessionSetupInterrupt:
    @pytest.mark.asyncio
    async def test_interrupt_during_session_setup_prevents_prompt(
        self,
        cursor_room: Callable[..., Awaitable[CursorRoom]],
    ) -> None:
        agent = SetupGatedAgent()
        room = await cursor_room(agent)
        sending = asyncio.create_task(room.send("start"))
        try:
            await agent.setup.received.wait()
            await room.adapter.on_interrupt("room-1", ControlMode.STOP)
            agent.setup.release.set()
            await asyncio.gather(sending, return_exceptions=True)
            await room.turns_finished()
        finally:
            agent.setup.release.set()
            await asyncio.gather(sending, return_exceptions=True)
        assert agent.prompts == []
