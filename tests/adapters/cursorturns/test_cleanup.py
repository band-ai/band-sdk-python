"""A Cursor turn's own cleanup ends the turn without waiting on itself."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest
from acp import RequestError

from band.core.exceptions import InputNotAccepted
from band.integrations.acp.client_adapter import ACPTurnTimeoutError
from tests.adapters.cursorturns.conftest import OpenCursor, cursor_session
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    active_turn,
    busy_provider,
    cancels_sent,
    cursor_adapter,
    decision_token,
    deny_command,
    occupy,
    select_command,
    terminated,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock

# A deadlock fails here instead of hanging the suite.
DEADLOCK_GUARD_S = 10


class TestCursorTurnCleanup:
    async def test_a_provider_error_frees_the_room(
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
            await session.send("hello again", room=ROOM_A)

        assert len(agent.prompts) == 2

    @pytest.mark.timeout(DEADLOCK_GUARD_S)
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_timeout_cleanup_run_by_a_child_frees_the_room(self) -> None:
        provider = busy_provider("prompt")
        async with controlled_clock() as clock:
            resources = AsyncExitStack()
            try:
                session = await clock.run_io(
                    resources.enter_async_context(
                        cursor_session(
                            provider.agent, turn_timeout_s=2, decision_timeout_s=1
                        )
                    )
                )
                sending = await resources.enter_async_context(
                    background(await clock.run_io(occupy(session, provider)))
                )
                assert not sending.done()

                await clock.elapse(2)
                with pytest.raises(ACPTurnTimeoutError):
                    await sending
                assert cancels_sent(provider.agent) == [provider.agent.session_ids()[0]]

                await clock.run_io(session.send("hello again", room=ROOM_A))
                assert len(provider.agent.prompts) == 2
            finally:
                provider.finish.set()
                await clock.run_io(resources.aclose())

    async def test_a_stale_turns_retirement_leaves_its_successor_running(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval", on_prompts=(1, 2))
        session = await open_cursor(provider.agent)
        adapter = cursor_adapter(session)
        await occupy(session, provider)
        stale = active_turn(session)
        await session.send(deny_command(decision_token(session)), room=ROOM_A)
        await terminated(session, stale)
        await occupy(session, provider)
        successor = active_turn(session)
        token = decision_token(session)

        # The stale turn's done callback, which only the event loop can order
        # after its successor took the room.
        adapter._forget_turn(stale)
        with pytest.raises(InputNotAccepted):
            await session.send("hello", room=ROOM_A)

        await session.send(select_command(token), room=ROOM_A)
        await terminated(session, successor)
        assert provider.agent.approved is True
        assert cancels_sent(provider.agent) == []
