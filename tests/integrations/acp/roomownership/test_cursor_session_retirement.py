"""A Cursor room forgets a retired session's state and settles its turn
before its process closes."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    AcpSession,
    accepted_work,
    started_acp_adapter,
)
from tests.mcpclient import crash_backend


def todo_sessions(session: AcpSession, room: str = "room-1") -> set[str]:
    """The sessions whose Cursor todos the room's live process still tracks."""
    client = session.runtime(room).client
    assert client is not None
    return set(cast(Any, client)._profile._todos_by_session)


class TestCursorSessionRetirement:
    @pytest.mark.asyncio
    async def test_a_retired_sessions_todos_are_forgotten(self) -> None:
        agent = FakeACPAgent().will_update_cursor_todos("ship it").will_say("ok")
        adapter = CursorACPAdapter(
            CursorACPAdapterConfig(command="fake-agent", inject_band_tools=True)
        )

        async with started_acp_adapter(adapter, agent) as session:
            await session.send("plan it", room="room-1")
            retired = session.session_id("room-1")
            await crash_backend(adapter._mcp)
            await session.send("after the crash", room="room-1")
            await session.retired_sessions_closed("room-1")

            assert todo_sessions(session) == {session.session_id("room-1")}
            assert retired != session.session_id("room-1")

    @pytest.mark.asyncio
    async def test_a_turn_still_running_at_cleanup_is_settled_before_its_process_closes(
        self,
    ) -> None:
        """A turn left running detached keeps updating todos; leaving the room
        cancels and joins it before the room's process, and its todos, are gone."""
        agent = FakeACPAgent()
        updating = agent.keeps_updating_cursor_todos()
        adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))

        async with started_acp_adapter(adapter, agent) as session:
            turn = asyncio.create_task(session.send("plan it", room="room-1"))
            await updating.wait()
            runtime = session.runtime("room-1")
            [detached] = accepted_work(session.room("room-1"))
            exiting = agent.exits_slowly()

            cleanup = asyncio.create_task(adapter.on_cleanup("room-1"))
            await exiting.received.wait()
            settled_before_exit = detached.done()
            exiting.release.set()
            await cleanup

            assert settled_before_exit
            assert runtime.closed and runtime.client is None
            assert "room-1" not in adapter._rooms
            await asyncio.gather(turn, return_exceptions=True)
