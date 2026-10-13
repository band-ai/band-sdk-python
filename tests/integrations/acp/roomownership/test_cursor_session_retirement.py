"""A Cursor room forgets a retired session's state and settles its turn
before its process closes."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    DEFAULT_ROOM,
    AcpSession,
    accepted_work,
)
from tests.integrations.acp.roomownership.kit import cursor
from tests.mcpclient import crash_backend


def todo_sessions(session: AcpSession) -> set[str]:
    """The sessions whose Cursor todos the room's live process still tracks."""
    client = session.runtime().client
    assert client is not None
    return set(cast(Any, client)._profile._todos_by_session)


class TestCursorSessionRetirement:
    @pytest.mark.asyncio
    async def test_a_retired_sessions_todos_are_forgotten(self) -> None:
        agent = FakeACPAgent().will_update_cursor_todos("ship it").will_say("ok")
        async with cursor(agent, inject_band_tools=True) as session:
            await session.send("plan it")
            retired = session.session_id(DEFAULT_ROOM)
            await crash_backend(session.adapter._mcp)
            await session.send("after the crash")
            await session.retired_sessions_closed()

            assert todo_sessions(session) == {session.session_id(DEFAULT_ROOM)}
            assert retired != session.session_id(DEFAULT_ROOM)

    @pytest.mark.asyncio
    async def test_a_turn_still_running_at_cleanup_is_settled_before_its_process_closes(
        self,
    ) -> None:
        """A turn left running detached keeps updating todos; leaving the room
        cancels and joins it before the room's process, and its todos, are gone."""
        agent = FakeACPAgent()
        updating = agent.keeps_updating_cursor_todos()
        async with cursor(agent) as session:
            turn = asyncio.create_task(session.send("plan it"))
            await updating.wait()
            runtime = session.runtime()
            [detached] = accepted_work(session.room())
            exiting = agent.exits_slowly()

            cleanup = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await exiting.received.wait()
            settled_before_exit = detached.done()
            exiting.release.set()
            await cleanup

            assert settled_before_exit
            assert runtime.closed and runtime.client is None
            assert DEFAULT_ROOM not in session.adapter._rooms
            await asyncio.gather(turn, return_exceptions=True)
