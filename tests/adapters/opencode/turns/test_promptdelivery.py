"""Required prompts decline through their native owner when delivery fails."""

from __future__ import annotations

import pytest

from band.runtime.tools.agent import AgentTools
from tests.adapters.audiences import AGENT, ALICE
from tests.adapters.hold import Hold
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    declined_questions,
    event_permission,
    event_question,
    event_session_idle,
    room_turn,
    run_single_turn,
    turn_finished,
)
from tests.core.automaticdelivery.kit import MembershipPeer
from tests.runtime.helpers import rest_client_over
from tests.toolkit.tasks import background


class TestPromptDelivery:
    @pytest.mark.parametrize(("read_status", "post_status"), [(503, 201), (200, 503)])
    async def test_permission_delivery_failure_declines_without_waiting_for_a_human(
        self, make_adapter, read_status: int, post_status: int
    ) -> None:
        peer = MembershipPeer([ALICE["id"], AGENT["id"]])
        peer.read_status, peer.post_status = read_status, post_status
        completion = Hold()
        client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "permission")]],
            reply_permission_events={
                "permission": [completion.wait, event_session_idle("sess-1")]
            },
        )
        adapter = make_adapter(client)
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools("room-1", rest)
            async with background(run_single_turn(adapter, tools)) as sending:
                try:
                    await completion.arrived()
                    turn = await room_turn(adapter)
                    assert [reply["reply"] for reply in client.permission_replies] == [
                        "reject"
                    ]
                    assert not turn.approvals.awaiting_human()
                    assert not tools.turn.complete
                finally:
                    completion.released.set()
                await sending
                await turn_finished(turn)

    @pytest.mark.parametrize(("read_status", "post_status"), [(503, 201), (200, 503)])
    async def test_question_delivery_failure_declines_without_waiting_for_a_human(
        self, make_adapter, read_status: int, post_status: int
    ) -> None:
        peer = MembershipPeer([ALICE["id"], AGENT["id"]])
        peer.read_status, peer.post_status = read_status, post_status
        completion = Hold()
        client = FakeOpencodeClient(
            prompt_event_sequences=[[event_question("sess-1", "question", "Which?")]],
            reply_question_events={
                "question": [completion.wait, event_session_idle("sess-1")]
            },
        )
        adapter = make_adapter(client)
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools("room-1", rest)
            async with background(run_single_turn(adapter, tools)) as sending:
                try:
                    await completion.arrived()
                    turn = await room_turn(adapter)
                    assert declined_questions(client) == ["question"]
                    assert not turn.approvals.awaiting_human()
                    assert not tools.turn.complete
                finally:
                    completion.released.set()
                await sending
                await turn_finished(turn)
