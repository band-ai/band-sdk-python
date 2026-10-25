"""Automatic recipient selection through real tools and REST serialization."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.delivery import (
    DeliveryFailedError,
    automatic_mentions,
    deliver_reply,
    finish_automatic_reply,
)
from band.core.exceptions import MessagePostError, MessagePostKind
from band.runtime.tools.agent import AgentTools
from tests.core.automaticdelivery.kit import (
    AGENT,
    ALICE,
    ANSWER,
    BOB,
    CAROL,
    ROOM,
    MembershipPeer,
    participant,
)
from tests.runtime.helpers import rest_client_over


class TestAutomaticAudience:
    async def test_a_stable_sender_id_rejoining_is_eligible_on_the_next_snapshot(
        self,
    ) -> None:
        peer = MembershipPeer([AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            assert await automatic_mentions(tools, [ALICE]) == []
            peer.members.append(ALICE)
            peer.snapshot.append(participant(ALICE))
            mentions = await automatic_mentions(tools, [ALICE, ALICE])
            await finish_automatic_reply(tools, ANSWER, mentions)

        assert peer.recipients == [[ALICE]]
        assert peer.reads == 2

    @pytest.mark.parametrize(
        ("members", "expected"),
        [([ALICE, BOB, AGENT], [ALICE, BOB]), ([BOB, CAROL, AGENT], [BOB])],
    )
    async def test_posts_once_to_remaining_original_senders(
        self, members: list[str], expected: list[str]
    ) -> None:
        peer = MembershipPeer(members)
        cached = [participant(ALICE), participant(BOB)]
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest, cached)
            mentions = await automatic_mentions(tools, [ALICE, "", BOB, ALICE])
            await finish_automatic_reply(tools, ANSWER, mentions)

        assert peer.recipients == [expected]
        assert tools.participants == cached
        assert tools.turn.replied
        assert peer.reads == 1

    async def test_unfiltered_departed_sender_reproduces_server_rejection(self) -> None:
        peer = MembershipPeer([BOB, AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            with pytest.raises(DeliveryFailedError) as caught:
                await deliver_reply(tools, ANSWER, [{"id": ALICE}, {"id": BOB}])

        assert isinstance(caught.value.cause, MessagePostError)
        assert caught.value.cause.kind == MessagePostKind.MENTION_NOT_IN_ROOM
        assert not tools.turn.complete
        assert peer.recipients == [[ALICE, BOB]]

    @pytest.mark.parametrize("text", [ANSWER, None, " \n"])
    async def test_all_original_senders_absent_settles_without_post(
        self, text: str | None
    ) -> None:
        peer = MembershipPeer([CAROL, AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            mentions = await automatic_mentions(tools, [ALICE, BOB])
            assert not tools.turn.complete
            await finish_automatic_reply(tools, text, mentions)

        assert tools.turn.complete
        assert not tools.turn.replied
        assert peer.posts == []

    @pytest.mark.parametrize("snapshot", [None, [], [{"id": ALICE}]])
    async def test_invalid_snapshot_fails_without_settlement(
        self, snapshot: Any
    ) -> None:
        peer = MembershipPeer([AGENT])
        peer.snapshot = snapshot
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            with pytest.raises(DeliveryFailedError):
                await automatic_mentions(tools, [ALICE])

        assert not tools.turn.complete
        assert peer.posts == []

    async def test_failed_snapshot_is_not_an_exhausted_audience(self) -> None:
        peer = MembershipPeer([AGENT])
        peer.read_status = 503
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            with pytest.raises(DeliveryFailedError):
                await automatic_mentions(tools, [ALICE])
        assert not tools.turn.complete
        assert peer.posts == []

    async def test_empty_original_audience_never_reads_or_settles(self) -> None:
        peer = MembershipPeer([AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            with pytest.raises(DeliveryFailedError):
                await automatic_mentions(tools, [""])
        assert not tools.turn.complete
        assert peer.reads == 0

    async def test_cancellation_during_lookup_propagates(self) -> None:
        peer = MembershipPeer([AGENT])
        peer.hold_read = True
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            lookup = asyncio.create_task(automatic_mentions(tools, [ALICE]))
            await peer.read_started.wait()
            lookup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await lookup
        assert not tools.turn.complete
        assert peer.posts == []

    async def test_departure_after_snapshot_fails_once_without_retry(self) -> None:
        peer = MembershipPeer([ALICE, AGENT])
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            mentions = await automatic_mentions(tools, [ALICE])
            peer.members.remove(ALICE)
            with pytest.raises(DeliveryFailedError):
                await finish_automatic_reply(tools, ANSWER, mentions)
        assert not tools.turn.complete
        assert peer.recipients == [[ALICE]]

    async def test_ambiguous_receipt_fails_once_without_settling(self) -> None:
        peer = MembershipPeer([ALICE, AGENT])
        peer.post_status = 200
        async with rest_client_over(peer.answer) as rest:
            tools = AgentTools(ROOM, rest)
            mentions = await automatic_mentions(tools, [ALICE])
            with pytest.raises(DeliveryFailedError):
                await finish_automatic_reply(tools, ANSWER, mentions)
        assert not tools.turn.complete
        assert peer.recipients == [[ALICE]]
