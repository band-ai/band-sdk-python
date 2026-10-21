"""Cursor's own decision notices address the senders the turn was admitted with."""

from __future__ import annotations

import asyncio

from band.integrations.acp.cursor import (
    DECISION_RESOLVED_TEMPLATE,
    DECISION_UNAUTHORIZED_MESSAGE,
)
from band.testing import FakeAgentTools
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    LIST_DECISIONS,
    WAIT_S,
    active_turn,
    busy_provider,
    command,
    decision_token,
    occupy,
    select_command,
    terminated,
    turn_tools,
)

ALICE_AND_BOB = ["alice", "bob"]


def posts(tools: FakeAgentTools) -> list[tuple[str, object]]:
    """Each room post as (text, whom it mentions)."""
    return [(post["content"], post["mentions"]) for post in tools.messages_sent]


def mentioned(tools: FakeAgentTools) -> list[object]:
    return [mentions for _, mentions in posts(tools)]


class TestCursorDecisionAudience:
    async def test_the_prompt_mentions_each_distinct_sender_of_a_batch_once(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)

        await occupy(session, provider, batch=True)

        assert mentioned(turn_tools(session)) == [ALICE_AND_BOB]

    async def test_a_later_message_does_not_change_who_the_timeout_notice_addresses(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent, decision_timeout_s=0.2)
        await occupy(session, provider, batch=True)
        tools = turn_tools(session)

        _, listing = await command(session, LIST_DECISIONS, sender_id="carol")
        async with asyncio.timeout(WAIT_S):
            await tools.until_said("timed out")

        assert mentioned(listing) == [["carol"]]
        assert mentioned(tools) == [ALICE_AND_BOB, ALICE_AND_BOB]

    async def test_being_notified_grants_no_authority_to_resolve_the_decision(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(
            provider.agent, decision_authorized_senders=frozenset({"alice"})
        )
        await occupy(session, provider, batch=True)
        turn = active_turn(session)
        token = decision_token(session)
        resolve = select_command(token)

        _, from_bob = await command(session, resolve, sender_id="bob")
        _, from_alice = await command(session, resolve, sender_id="alice")
        await terminated(session, turn)

        assert posts(from_bob) == [(DECISION_UNAUTHORIZED_MESSAGE, ["bob"])]
        assert posts(from_alice) == [
            (
                DECISION_RESOLVED_TEMPLATE.format(kind="permission", token=token),
                ["alice"],
            )
        ]
        assert provider.agent.approved is True
