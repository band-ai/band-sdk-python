"""Codex batch notices use fresh membership without changing turn ownership."""

from __future__ import annotations

import pytest
from band_rest.core.api_error import ApiError

from band.core.protocols import FailureMetadataKey
from band.core.types import AgentInput, HistoryProvider
from band.runtime.tools import BandTool
from band.runtime.tools.agent import AgentTools
from tests.adapters.codex.kit import approval_tokens_from_chat
from tests.adapters.codexbatch.kit import (
    HELPER,
    ROOM_ID,
    CodexBatchRoom,
    OpenRoom,
    approval_request,
)
from tests.adapters.codexturns import tool_call_request, turn_completed
from tests.core.automaticdelivery.kit import AGENT, MembershipPeer
from tests.runtime.helpers import rest_client_over


def batch_input(room: CodexBatchRoom, tools: AgentTools) -> AgentInput:
    """Keep the room's originals while replacing only the delivery boundary."""
    messages = room.alice_helper_alice()
    return AgentInput(
        msg=messages[0],
        messages=messages,
        tools=tools,
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=ROOM_ID,
    )


class TestDepartedAudience:
    @pytest.mark.parametrize(
        ("mode", "decision"),
        [("auto_accept", "accept"), ("auto_decline", "decline")],
    )
    async def test_policy_notice_reaches_only_the_remaining_original_sender(
        self, codex_room: OpenRoom, mode: str, decision: str
    ) -> None:
        room = await codex_room(
            approval_request(10),
            tool_call_request(11, BandTool.NO_REPLY, {}),
            turn_completed(),
            approval_mode=mode,
            approval_text_notifications=True,
        )
        peer = MembershipPeer([HELPER["id"], AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM_ID, rest)
            await room.adapter.on_messages(batch_input(room, tools))

        assert peer.recipients == [[HELPER["id"]]]
        assert room.decisions == [(10, decision)]

    async def test_manual_prompt_reaches_the_remaining_sender_and_awaits_approval(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            approval_request(10),
            tool_call_request(11, BandTool.NO_REPLY, {}),
            turn_completed(),
        )
        peer = MembershipPeer([HELPER["id"], AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM_ID, rest)
            await room.adapter.on_messages(batch_input(room, tools))
            assert peer.recipients == [[HELPER["id"]]]
            assert not tools.turn.complete
            token = approval_tokens_from_chat([peer.posts[0]["message"]["content"]])[0]
            await room.control(f"/approve {token}")
            await room.settled()

        assert room.decisions == [(10, "accept")]

    @pytest.mark.parametrize(
        ("mode", "decision"),
        [("manual", "decline"), ("auto_accept", "accept"), ("auto_decline", "decline")],
    )
    async def test_exhausted_audience_settles_without_posting(
        self, codex_room: OpenRoom, mode: str, decision: str
    ) -> None:
        room = await codex_room(
            approval_request(10),
            turn_completed(),
            approval_mode=mode,
            approval_text_notifications=True,
        )
        peer = MembershipPeer([AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM_ID, rest)
            await room.adapter.on_messages(batch_input(room, tools))

        assert room.decisions == [(10, decision)]
        assert peer.posts == []
        assert tools.turn.complete
        assert not tools.turn.replied

    async def test_completion_roster_failure_is_not_reported_as_provider_failure(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(turn_completed())
        peer = MembershipPeer([HELPER["id"], AGENT])
        peer.read_status = 503
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM_ID, rest)
            with pytest.raises(ApiError):
                await room.adapter.on_messages(batch_input(room, tools))

        assert not tools.turn.complete
        assert peer.posts == []
        assert not any(
            FailureMetadataKey.FAILURE in event["event"].get("metadata", {})
            for event in peer.events
        )
