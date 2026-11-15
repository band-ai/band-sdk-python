"""Successful completion preserves reply and work ledger semantics."""

from __future__ import annotations

import asyncio

import pytest

from band.core.delivery import automatic_mentions, finish_automatic_reply
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.types import TurnEffect
from tests.core.automaticdelivery.kit import (
    AGENT,
    ALICE,
    ANSWER,
    ROOM,
    MembershipPeer,
)
from tests.runtime.helpers import rest_client_over


class TestAutomaticCompletion:
    async def test_completed_real_work_still_relays_visible_final_text(self) -> None:
        peer = MembershipPeer([ALICE, AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            tools.turn.record(TurnEffect.ACT)
            assert tools.turn.complete
            mentions = await automatic_mentions(tools, [ALICE])
            await finish_automatic_reply(tools, ANSWER, mentions)
        assert peer.recipients == [[ALICE]]
        assert tools.turn.replied

    @pytest.mark.parametrize("effect", [TurnEffect.REPLY, TurnEffect.DECLINE])
    async def test_existing_reply_or_decline_suppresses_final_post(
        self, effect: TurnEffect
    ) -> None:
        peer = MembershipPeer([ALICE, AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            tools.turn.record(effect)
            await finish_automatic_reply(tools, ANSWER, [{"id": ALICE}])
        assert peer.posts == []

    @pytest.mark.parametrize("text", [None, "", " \n"])
    async def test_present_sender_without_visible_final_text_still_owes_reply(
        self, text: str | None
    ) -> None:
        peer = MembershipPeer([ALICE, AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            mentions = await automatic_mentions(tools, [ALICE])
            await finish_automatic_reply(tools, text, mentions)
        assert not tools.turn.complete
        assert peer.posts == []

    @pytest.mark.parametrize("effect", [TurnEffect.REPLY, TurnEffect.DECLINE])
    async def test_reply_claimed_during_lookup_suppresses_fallback(
        self, effect: TurnEffect
    ) -> None:
        peer = MembershipPeer([ALICE, AGENT])
        peer.hold_read = True
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            lookup = asyncio.create_task(automatic_mentions(tools, [ALICE]))
            try:
                await peer.read_started.wait()
                tools.turn.record(effect)
            finally:
                peer.release_read.set()
                mentions = await lookup
            await finish_automatic_reply(tools, ANSWER, mentions)
        assert tools.turn.replied
        assert peer.posts == []
