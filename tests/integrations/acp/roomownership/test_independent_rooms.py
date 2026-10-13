"""One room's held start or exit never holds another room's turn."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter


class TestIndependentRooms:
    """One room's held start or exit never holds another room's turn."""

    @pytest.mark.asyncio
    async def test_a_held_start_does_not_hold_another_room(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            starting = agent.holds_next_initialize()
            held = asyncio.create_task(session.send("hello", room="room-a"))
            await starting.received.wait()

            await session.send("hello", room="room-b")

            assert not held.done()
            assert session.room("room-b").live_session is not None
            starting.release.set()
            await held

    @pytest.mark.asyncio
    async def test_a_held_exit_does_not_hold_another_room(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello", room="room-a")
            await session.send("hello", room="room-b")
            exiting = agent.exits_slowly()
            cleanup = asyncio.create_task(session.adapter.on_cleanup("room-a"))
            await exiting.received.wait()

            await session.send("again", room="room-b")

            assert not cleanup.done()
            exiting.release.set()
            await cleanup
