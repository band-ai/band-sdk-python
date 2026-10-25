"""Membership-aware Claude notices through real AgentTools and REST."""

from __future__ import annotations

import pytest
from band_rest.core.api_error import ApiError

from band.core.protocols import TurnResultAlreadyReported
from band.runtime.tools.agent import AgentTools
from tests.adapters.claude_sdk.batch.kit import (
    ADMIN,
    HELPER,
    LIST_FILES,
    OpenRoom,
    alice_helper_alice,
)
from tests.adapters.claude_sdk.fakecli import EndTurn
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, with_approvals
from tests.core.automaticdelivery.kit import AGENT, MembershipPeer
from tests.runtime.helpers import rest_client_over


class TestDepartedAudience:
    async def test_completion_roster_failure_does_not_become_provider_failure(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        peer = MembershipPeer([HELPER["id"], AGENT])
        peer.read_status = 503
        room.claude.script([EndTurn()])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(room.room_id, rest)
            with pytest.raises(ApiError):
                await room.send_batch(*alice_helper_alice(room), tools=tools)

        assert not tools.turn.complete
        assert peer.posts == []

    @pytest.mark.parametrize("mode", ["auto_accept", "auto_decline"])
    async def test_policy_notice_reaches_remaining_original_sender(
        self, claude_room: OpenRoom, mode: str
    ) -> None:
        room = await claude_room(with_approvals(mode))
        peer = MembershipPeer([HELPER["id"], AGENT])
        room.claude.script([WRITE_NOTE, EndTurn()])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(room.room_id, rest)
            with pytest.raises(TurnResultAlreadyReported):
                await room.send_batch(*alice_helper_alice(room), tools=tools)

        assert peer.recipients == [[HELPER["id"]]]

    async def test_manual_prompt_reaches_remaining_sender_after_departure(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        peer = MembershipPeer([HELPER["id"], AGENT])
        room.claude.script([LIST_FILES, EndTurn()])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(room.room_id, rest)
            await room.send_batch(*alice_helper_alice(room), tools=tools)
            assert peer.recipients == [[HELPER["id"]]]
            assert not tools.turn.complete
            await room.send("/approve a-1", sender=ADMIN)
            await room.settled()

    @pytest.mark.parametrize("mode", ["manual", "auto_accept", "auto_decline"])
    async def test_exhausted_audience_finishes_without_prompt_or_false_decline_notice(
        self, claude_room: OpenRoom, mode: str
    ) -> None:
        room = await claude_room(with_approvals(mode))
        peer = MembershipPeer([AGENT])
        room.claude.script([WRITE_NOTE, EndTurn()])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(room.room_id, rest)
            await room.send_batch(*alice_helper_alice(room), tools=tools)
            await room.settled()

        assert peer.posts == []
        assert tools.turn.complete
        assert not tools.turn.replied
        assert room.adapter._notified_declines == {}
