"""Cursor's own decision notices address the senders the turn was admitted with."""

from __future__ import annotations

from contextlib import AsyncExitStack

import pytest

from band.integrations.acp.cursor import (
    DECISION_RESOLVED_TEMPLATE,
    DECISION_UNAUTHORIZED_MESSAGE,
)
from band.testing import FakeAgentTools
from tests.adapters.cursorturns.conftest import OpenCursor, cursor_session
from tests.adapters.cursorturns.kit import (
    LIST_DECISIONS,
    active_turn,
    busy_provider,
    command,
    decision_token,
    occupy,
    select_command,
    terminated,
    turn_tools,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

ALICE_AND_BOB = [{"id": "alice"}, {"id": "bob"}]


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

    @pytest.mark.timeout(10)
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_later_message_does_not_change_who_the_timeout_notice_addresses(
        self,
    ) -> None:
        provider = busy_provider("approval")
        decision_timeout_s = 1.0
        async with controlled_clock() as clock:
            resources = AsyncExitStack()
            try:
                session = await clock.run_io(
                    resources.enter_async_context(
                        cursor_session(
                            provider.agent,
                            decision_timeout_s=decision_timeout_s,
                            turn_timeout_s=10,
                        )
                    )
                )
                await clock.run_io(occupy(session, provider, batch=True))
                turn = active_turn(session)
                tools = turn_tools(session)
                _, listing = await command(session, LIST_DECISIONS, sender_id="carol")
                assert mentioned(tools) == [ALICE_AND_BOB]

                await clock.elapse(decision_timeout_s)
                await tools.until_said("timed out")
                await terminated(session, turn)

                assert mentioned(listing) == [["carol"]]
                assert mentioned(tools) == [ALICE_AND_BOB, ALICE_AND_BOB]
                assert provider.agent.approved is False
            finally:
                provider.finish.set()
                await clock.run_io(resources.aclose())

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
