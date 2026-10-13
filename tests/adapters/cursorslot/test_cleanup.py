"""A Cursor turn's own cleanup ends the turn without waiting on itself."""

from __future__ import annotations

import asyncio

import pytest
from acp import RequestError

from band.integrations.acp.client_adapter import ACPTurnTimeoutError
from tests.adapters.cursorslot.conftest import OpenCursor
from tests.adapters.cursorslot.kit import (
    ROOM_A,
    ROOM_B,
    active_turn,
    busy_provider,
    cursor_adapter,
    decision_token,
    occupy,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent

# A deadlock fails here instead of hanging the suite.
DEADLOCK_GUARD_S = 10


class TestCursorTurnCleanup:
    async def test_a_provider_error_frees_the_slot(
        self, open_cursor: OpenCursor
    ) -> None:
        agent = FakeACPAgent()

        @agent.on_prompt
        async def fail_first(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                raise RequestError.internal_error()

        session = await open_cursor(agent)

        async with asyncio.timeout(DEADLOCK_GUARD_S):
            with pytest.raises(RequestError):
                await session.send("hello", room=ROOM_A)
            await session.send("hello again", room=ROOM_B)

        assert len(agent.prompts) == 2

    async def test_a_timeout_cleanup_run_by_a_child_frees_the_slot(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(
            provider.agent, turn_timeout_s=0.05, decision_timeout_s=0.01
        )

        async with asyncio.timeout(DEADLOCK_GUARD_S):
            with pytest.raises(ACPTurnTimeoutError):
                await session.send("hello", room=ROOM_A)
            await session.send("hello again", room=ROOM_B)

        assert len(provider.agent.prompts) == 2

    async def test_a_stale_turns_retirement_leaves_its_successor_running(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval", on_prompts=(1, 2))
        session = await open_cursor(provider.agent)
        adapter = cursor_adapter(session)
        await occupy(session, provider)
        stale = active_turn(session)
        await session.send(f"/cursor deny {decision_token(session)}", room=ROOM_A)
        await stale.terminated()
        await occupy(session, provider)
        successor = active_turn(session)
        token = decision_token(session)

        adapter._retire_turn(stale)
        stale.request_cancel()

        assert adapter._active_turn is successor
        assert adapter._cursor_profile.extension_session_id == successor.session_id
        await session.send(f"/cursor select {token} allow-1", room=ROOM_A)
        await successor.terminated()
        assert provider.agent.approved is True
        assert provider.agent.cancelled_sessions == []
