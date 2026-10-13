"""STOP, INTERRUPT and room retirement cancel Cursor's provider task."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.cursor_acp import CursorACPAdapter
from band.client.streaming import ControlMode
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
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent

Stop = Callable[[CursorACPAdapter, str], Awaitable[None]]


async def stop(adapter: CursorACPAdapter, room_id: str) -> None:
    await adapter.on_interrupt(room_id, ControlMode.STOP)


async def interrupt(adapter: CursorACPAdapter, room_id: str) -> None:
    await adapter.on_interrupt(room_id, ControlMode.INTERRUPT)


async def retire(adapter: CursorACPAdapter, room_id: str) -> None:
    await adapter.on_cleanup(room_id)


class TestCursorProviderCancellation:
    @pytest.mark.parametrize("stopping", [stop, interrupt, retire])
    @pytest.mark.parametrize("kind", ["prompt", "approval"])
    async def test_the_peer_is_told_to_cancel_the_active_prompt(
        self, open_cursor: OpenCursor, kind: BusyKind, stopping: Stop
    ) -> None:
        provider = busy_provider(kind)
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider)
        turn = active_turn(session)

        await stopping(cursor_adapter(session), ROOM_A)
        await turn.terminated()
        await asyncio.gather(sending, return_exceptions=True)

        assert provider.agent.cancelled_sessions == [provider.agent.session_ids()[0]]

    async def test_a_repeated_stop_cancels_the_provider_task_once(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        teardown = hold_cancellation(session)
        await occupy(session, provider)
        turn = active_turn(session)
        assert turn.task is not None

        await stop(cursor_adapter(session), ROOM_A)
        await teardown.received.wait()
        await stop(cursor_adapter(session), ROOM_A)
        teardown.release.set()
        await turn.terminated()

        assert turn.task.cancelling() == 1
        assert provider.agent.cancelled_sessions == [provider.agent.session_ids()[0]]

    async def test_stopping_another_room_leaves_the_active_prompt_running(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)
        turn = active_turn(session)

        await stop(cursor_adapter(session), ROOM_B)

        assert not turn.cancel_requested
        assert provider.agent.cancelled_sessions == []

    async def test_cancelling_before_the_task_starts_never_reaches_the_peer(
        self, open_cursor: OpenCursor
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        session = await open_cursor(agent)
        adapter = cursor_adapter(session)

        # Queued after the delivery's first step and ahead of the provider
        # task's, so the task is cancelled before its first instruction.
        sending = asyncio.create_task(session.send("hello", room=ROOM_A))
        asyncio.get_running_loop().call_soon(
            lambda: active_turn(session).request_cancel()
        )
        await asyncio.gather(sending, return_exceptions=True)
        await session.send("hello again", room=ROOM_B)

        assert agent.session_ids() == ["fake-session-1"]
        assert len(agent.prompts) == 1
        assert agent.cancelled_sessions == []
        assert adapter._active_turn is None
