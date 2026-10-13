"""A process recovery retired never reaches the room, even while the turn
it served is still reporting its failure."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM, FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import SlowFailureReports
from tests.integrations.acp.roomownership.kit import cursor, holds_prompt


class TestCursorRecoveredTurn:
    @pytest.mark.asyncio
    async def test_a_question_from_a_timed_out_turns_process_is_never_posted(
        self,
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

            await agent.ask_cursor_question(session_id, "q1", "a", "b")
            failing.reporting.release.set()
            prompting.release.set()
            await asyncio.gather(sending, return_exceptions=True)

        assert not [sent for sent in failing.messages_sent if "q1" in sent["content"]]
