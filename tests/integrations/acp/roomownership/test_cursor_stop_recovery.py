"""A Cursor stop recovers its own process whatever the agent answers."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    accepted_work_finished,
    released,
)
from tests.integrations.acp.roomownership.kit import cursor, holds_prompt


class TestCursorStopRecovery:
    """Neither the cancel nor an answered prompt shows Cursor's native tools
    ended, so only the release of the turn's own process reopens the room."""

    @pytest.mark.asyncio
    async def test_an_answered_prompt_does_not_confirm_the_stop(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        prompting = holds_prompt(agent, 1)
        async with cursor(agent) as session:
            turn = asyncio.create_task(session.send("work"))
            await prompting.received.wait()
            room, runtime = session.room(), session.runtime()
            generation = room.generation
            turns = session.adapter.backend_turns

            turns.cancel(DEFAULT_ROOM)
            await turns.drain()

            # Bound to the turn's own residency before any admission waits on it.
            stopped = turns.unconfirmed_handle(DEFAULT_ROOM)
            assert stopped is not None
            assert (stopped.runtime, stopped.generation) == (runtime, generation)
            assert turns.blocker(DEFAULT_ROOM) is not None

            prompting.release.set()
            await asyncio.gather(turn, return_exceptions=True)
            await accepted_work_finished(room)
            await released(room)

            served = await session.send("again")
            await accepted_work_finished(room)

            assert runtime.closed
            assert served.errors == []
            assert session.runtime() is not runtime
            assert room.generation == generation + 1
