"""Cursor permission delivery preserves current membership and turn ownership."""

from __future__ import annotations

import httpx
import pytest

from band.client.rest import AsyncRestClient
from band.runtime.tools.agent import AgentTools
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    active_turn,
    command,
    cursor_adapter,
    deny_command,
    select_command,
    terminated,
)
from tests.adapters.cursorturns.membershipkit import (
    ALICE,
    BOB,
    audience_input,
    departed_tools,
    permission_work,
)
from tests.core.automaticdelivery.kit import AGENT, MembershipPeer
from tests.integrations.acp.acp_toolkit.harness import (
    decision_token,
    pending_decisions,
)
from tests.toolkit.tasks import background

pytestmark = pytest.mark.timeout(10)


class TestCursorMembership:
    async def test_a_departed_originator_does_not_block_the_remaining_sender(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = permission_work()
        session = await open_cursor(provider.agent)
        adapter = cursor_adapter(session)
        tools = await departed_tools(ALICE)
        try:
            await adapter.on_messages(audience_input(tools, ALICE, BOB, ALICE))
            turn = active_turn(session)
            assert [post["mentions"] for post in tools.messages_sent] == [[{"id": BOB}]]

            token = decision_token(tools.reply.texts[0])
            await command(session, select_command(token), sender_id=BOB)
            await provider.finishing.received.wait()
            assert provider.agent.approved is True
            assert len(provider.agent.permission_responses) == 1

            provider.finishing.release.set()
            await terminated(session, turn)
        finally:
            provider.finishing.release.set()

    async def test_no_originators_declines_each_request_and_settles_only_at_completion(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = permission_work(requests=2)
        session = await open_cursor(provider.agent)
        adapter = cursor_adapter(session)
        tools = await departed_tools(ALICE, BOB)
        try:
            await adapter.on_messages(audience_input(tools, ALICE, BOB))
            turn = active_turn(session)
            await provider.finishing.received.wait()

            assert provider.agent.approved is False
            assert len(provider.agent.permission_responses) == 2
            assert pending_decisions(adapter) == {}
            assert tools.messages_sent == []
            assert not tools.turn.complete
            assert failure_reports(tools) == []

            provider.finishing.release.set()
            await terminated(session, turn)

            assert tools.turn.complete
            assert not tools.turn.replied
            assert tools.messages_sent == []
            assert failure_reports(tools) == []
        finally:
            provider.finishing.release.set()

    async def test_a_remaining_originator_still_requires_a_reply_after_native_decline(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = permission_work()
        session = await open_cursor(provider.agent)
        adapter = cursor_adapter(session)
        tools = await departed_tools(BOB)
        try:
            await adapter.on_messages(audience_input(tools, ALICE, BOB))
            turn = active_turn(session)
            token = decision_token(tools.reply.texts[0])
            await command(session, deny_command(token), sender_id=ALICE)
            await provider.finishing.received.wait()
            assert not tools.turn.complete
            assert failure_reports(tools) == []

            provider.finishing.release.set()
            await terminated(session, turn)

            assert provider.agent.approved is False
            assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        finally:
            provider.finishing.release.set()

    async def test_a_control_claim_during_the_snapshot_owns_the_native_answer(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = permission_work()
        session = await open_cursor(provider.agent, inject_band_tools=False)
        adapter = cursor_adapter(session)
        peer = MembershipPeer([BOB, AGENT])
        peer.hold_read = True
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(peer.answer)
        ) as http:
            rest = AsyncRestClient(
                api_key="test-key",
                base_url="https://membership.test",
                httpx_client=http,
            )
            tools = AgentTools(ROOM_A, rest)
            async with background(
                adapter.on_messages(audience_input(tools, ALICE, BOB))
            ) as sending:
                try:
                    await peer.read_started.wait()
                    turn = active_turn(session)
                    [token] = pending_decisions(adapter)[ROOM_A]
                    await command(session, select_command(token), sender_id=BOB)
                    peer.release_read.set()
                    await sending
                    await provider.finishing.received.wait()

                    assert peer.posts == []
                    assert provider.agent.approved is True
                    assert len(provider.agent.permission_responses) == 1

                    provider.finishing.release.set()
                    await terminated(session, turn)
                    assert peer.posts == []
                    assert len(provider.agent.permission_responses) == 1
                finally:
                    peer.release_read.set()
                    provider.finishing.release.set()
                    await adapter.on_cleanup(ROOM_A)
