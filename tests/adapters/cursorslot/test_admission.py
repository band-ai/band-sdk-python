"""Cursor admits one provider turn at a time, without queueing the caller."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from tests.adapters.cursorslot.conftest import OpenCursor
from tests.adapters.cursorslot.kit import (
    ROOM_A,
    ROOM_B,
    BusyKind,
    active_turn,
    busy_provider,
    cursor_adapter,
    hold_cancellation,
    occupy,
    refusing_turn_tasks,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class TestCursorProviderAdmission:
    @pytest.mark.parametrize("kind", ["prompt", "approval"])
    async def test_another_rooms_input_is_refused_while_a_prompt_is_active(
        self, open_cursor: OpenCursor, kind: BusyKind
    ) -> None:
        provider = busy_provider(kind)
        session = await open_cursor(provider.agent)
        await occupy(session, provider)

        with pytest.raises(InputNotAccepted):
            await session.send("hello", room=ROOM_B)

        assert provider.agent.session_ids() == ["fake-session-1"]
        assert len(provider.agent.prompts) == 1
        assert session.last_reply.outline == []
        assert session.last_reply.texts == []

    async def test_the_slot_stays_taken_until_the_cancelled_task_terminates(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        teardown = hold_cancellation(session)
        sending = await occupy(session, provider)
        turn = active_turn(session)

        await cursor_adapter(session).on_interrupt(ROOM_A, ControlMode.STOP)
        await teardown.received.wait()
        with pytest.raises(InputNotAccepted):
            await session.send("hello", room=ROOM_B)

        teardown.release.set()
        await turn.terminated()
        await asyncio.gather(sending, return_exceptions=True)
        await session.send("hello again", room=ROOM_B)

        assert len(provider.agent.prompts) == 2

    async def test_a_refused_reservation_is_rolled_back_when_the_task_cannot_start(
        self, open_cursor: OpenCursor
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        session = await open_cursor(agent)

        with refusing_turn_tasks(), pytest.raises(RuntimeError):
            await session.send("hello", room=ROOM_A)
        await session.send("hello again", room=ROOM_B)

        assert len(agent.prompts) == 1
