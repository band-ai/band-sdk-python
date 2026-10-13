"""A process recovery retired never reaches the room, even while the turn
it served is still reporting its failure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import SlowFailureReports
from tests.integrations.acp.roomownership.kit import (
    CANCELLED,
    ask_mode,
    ask_shell_permission,
    cursor,
    decisions,
    holds_prompt,
)

Ask = Callable[[FakeACPAgent, str], Awaitable[dict[str, object]]]


# Each decision the retired process asks for, answered as the agent sees it.
ASKS: dict[str, Ask] = {"question": ask_mode, "permission": ask_shell_permission}


class TestCursorRecoveredTurn:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("ask", ASKS.values(), ids=ASKS.keys())
    async def test_a_decision_from_a_timed_out_turns_process_is_never_posted(
        self, ask: Ask
    ) -> None:
        agent = FakeACPAgent()
        prompting = holds_prompt(agent, 1)
        async with cursor(agent, turn_timeout_s=0.2, decision_timeout_s=0.1) as session:
            failing = SlowFailureReports(lost=0)
            sending = asyncio.create_task(session.send("work", tools=failing))
            await prompting.received.wait()
            session_id = session.session_id(DEFAULT_ROOM)
            # The timeout retired the process; its failure post is in flight.
            await failing.reporting.received.wait()

            stale = await ask(agent, session_id)
            failing.reporting.release.set()
            prompting.release.set()
            await asyncio.gather(sending, return_exceptions=True)

        assert stale == CANCELLED
        assert decisions(failing.reply) == []
