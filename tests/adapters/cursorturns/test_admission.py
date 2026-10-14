"""Cursor admits one provider turn per room at a time, without queueing the caller."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    BusyKind,
    active_turn,
    busy_provider,
    cursor_adapter,
    hold_cancellation,
    occupy,
    refusing_turn_tasks,
    stop,
    terminated,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class TestCursorRoomAdmission:
    @pytest.mark.parametrize("kind", ["prompt", "approval"])
    async def test_the_rooms_next_input_is_refused_while_its_prompt_is_active(
        self, open_cursor: OpenCursor, kind: BusyKind
    ) -> None:
        provider = busy_provider(kind)
        session = await open_cursor(provider.agent)
        await occupy(session, provider)

        with pytest.raises(InputNotAccepted):
            await session.send("hello", room=ROOM_A)

        assert provider.agent.session_ids() == ["fake-session-1"]
        assert len(provider.agent.prompts) == 1
        assert session.last_reply.outline == []
        assert session.last_reply.texts == []

    async def test_the_room_stays_taken_until_the_cancelled_task_terminates(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        teardown = hold_cancellation(session)
        sending = await occupy(session, provider)
        turn = active_turn(session)

        await stop(cursor_adapter(session), ROOM_A)
        await teardown.received.wait()
        with pytest.raises(InputNotAccepted):
            await session.send("hello", room=ROOM_A)

        teardown.release.set()
        await terminated(turn)
        await asyncio.gather(sending, return_exceptions=True)
        provider.finish.set()
        await session.send("hello again", room=ROOM_A)

        assert len(provider.agent.prompts) == 2

    async def test_a_refused_task_launch_leaves_the_room_free(
        self, open_cursor: OpenCursor
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        session = await open_cursor(agent)

        with refusing_turn_tasks(), pytest.raises(RuntimeError):
            await session.send("hello", room=ROOM_A)
        await session.send("hello again", room=ROOM_A)

        assert len(agent.prompts) == 1
