"""A Cursor turn released on a question still answers for its message when
its room closes: woken, it reports its own outcome within the settle grace."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from acp import RequestError

from band.integrations.acp.client_adapter import ACPClientAdapter
from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, FakeACPAgent
from tests.integrations.acp.roomownership.kit import TEST_GUARD_S, cursor


async def stop(adapter: ACPClientAdapter) -> None:
    await adapter.cleanup_all()


async def leave(adapter: ACPClientAdapter) -> None:
    await adapter.on_cleanup(DEFAULT_ROOM)


CLOSINGS = pytest.mark.parametrize("closing", [stop, leave])


class TestDetachedTurnOnClose:
    @CLOSINGS
    async def test_a_woken_turn_reports_its_own_failure(
        self, closing: Callable[[ACPClientAdapter], Awaitable[None]]
    ) -> None:
        agent = FakeACPAgent()
        failure = RequestError.internal_error()

        @agent.on_prompt
        async def ask_then_fail(peer: FakeACPAgent, session_id: str) -> None:
            await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
            raise failure

        async with cursor(agent) as session:
            asked = await session.send_judged("ask")
            async with asyncio.timeout(TEST_GUARD_S):
                await closing(session.adapter)

        assert asked.errors == [str(failure)]
