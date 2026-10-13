"""A process recovery retired never reaches the room, even while the turn
it served is still reporting its failure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from acp.schema import ToolCallUpdate

from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, FakeACPAgent
from tests.integrations.acp.acp_toolkit.agent import allow_or_reject
from tests.integrations.acp.acp_toolkit.harness import SlowFailureReports
from tests.integrations.acp.roomownership.kit import cursor, holds_prompt

# Each decision the retired process asks for, and the word its room prompt names.
RETIRED_TOOL = "retired-shell"
Ask = Callable[[FakeACPAgent, str], Awaitable[Any]]
ASKS: dict[str, tuple[Ask, str]] = {
    "question": (
        lambda agent, session_id: agent.ask_cursor_question(session_id, "q1", "a", "b"),
        "q1",
    ),
    "permission": (
        lambda agent, session_id: agent.ask_permission(
            session_id,
            ToolCallUpdate(tool_call_id="tc-retired", title=RETIRED_TOOL),
            allow_or_reject(),
        ),
        RETIRED_TOOL,
    ),
}


class TestCursorRecoveredTurn:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("asked", ASKS.values(), ids=ASKS.keys())
    async def test_a_decision_from_a_timed_out_turns_process_is_never_posted(
        self, asked: tuple[Ask, str]
    ) -> None:
        ask, named = asked
        agent = FakeACPAgent()
        prompting = holds_prompt(agent, 1)
        async with cursor(agent, turn_timeout_s=0.2, decision_timeout_s=0.1) as session:
            failing = SlowFailureReports(lost=0)
            sending = asyncio.create_task(session.send("work", tools=failing))
            await prompting.received.wait()
            session_id = session.session_id(DEFAULT_ROOM)
            # The timeout retired the process; its failure post is in flight.
            await failing.reporting.received.wait()

            await ask(agent, session_id)
            failing.reporting.release.set()
            prompting.release.set()
            await asyncio.gather(sending, return_exceptions=True)

        assert not [sent for sent in failing.messages_sent if named in sent["content"]]
