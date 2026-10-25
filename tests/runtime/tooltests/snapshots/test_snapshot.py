from __future__ import annotations

import asyncio

import pytest
from band_rest.core import ApiError

from band.client.rest import ParsingError
from band.runtime.cycle import TurnScope
from band.runtime.tools import AgentTools
from band.testing.fake_tools import FakeAgentTools
from tests.conftest import make_participant_added_event
from tests.runtime.scheduler.driver import attempt_delivery
from tests.runtime.tooltests.snapshots.kit import AGENT, ALICE, BOB, ROOM, SnapshotPeer


class TestParticipantSnapshot:
    async def test_fresh_reads_leave_both_cached_rosters_unchanged(self) -> None:
        peer = SnapshotPeer({"data": [BOB, AGENT]})
        async with peer.context() as ctx:
            tools = AgentTools.from_context(ctx)
            tools_before = tools.participants
            context_before = ctx.participants
            assert [p.id for p in await tools.get_participants_snapshot()] == [
                BOB["id"],
                AGENT["id"],
            ]
            peer.body = {"data": [AGENT]}
            assert [p.id for p in await tools.get_participants_snapshot()] == [
                AGENT["id"]
            ]
            assert tools.participants == tools_before
            assert ctx.participants == context_before
            assert len(peer.requests) == 2

    async def test_participant_event_wins_over_a_blocked_snapshot(self) -> None:
        peer = SnapshotPeer({"data": [ALICE, AGENT]})
        async with peer.context() as ctx:
            await ctx.hydrate()
            peer.release.clear()
            peer.arrived.clear()
            tools = AgentTools.from_context(ctx)
            tools_before = tools.participants
            lookup = asyncio.create_task(tools.get_participants_snapshot())
            try:
                await peer.arrived.wait()
                await attempt_delivery(
                    ctx,
                    make_participant_added_event(
                        room_id=ROOM,
                        participant_id=BOB["id"],
                        name=BOB["name"],
                        type=BOB["type"],
                    ),
                )
                after_event = ctx.participants
                assert BOB["id"] in [p["id"] for p in after_event]
                peer.release.set()
                assert [p.id for p in await lookup] == [ALICE["id"], AGENT["id"]]
                assert ctx.participants == after_event
                assert tools.participants == tools_before
            finally:
                peer.release.set()
                await lookup

    async def test_terminated_scope_prevents_the_snapshot_read(self) -> None:
        peer = SnapshotPeer({"data": [AGENT]})
        async with peer.context() as ctx:
            ctx.current_scope = TurnScope(0)
            tools = AgentTools.from_context(ctx)
            ctx.current_scope.terminate()
            with pytest.raises(asyncio.CancelledError):
                await tools.get_participants_snapshot()
            assert peer.requests == []

    async def test_scope_termination_during_read_prevents_exhaustion_result(
        self,
    ) -> None:
        peer = SnapshotPeer({"data": [AGENT]}, blocked=True)
        async with peer.context() as ctx:
            ctx.current_scope = TurnScope(0)
            tools = AgentTools.from_context(ctx)
            lookup = asyncio.create_task(tools.get_participants_snapshot())
            try:
                await peer.arrived.wait()
                ctx.current_scope.terminate()
                peer.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await lookup
            finally:
                peer.release.set()
                await asyncio.gather(lookup, return_exceptions=True)

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"data": None},
            {"data": []},
            {"data": [ALICE, ALICE]},
            {"data": [{**ALICE, "id": " "}]},
            {"data": [{"name": "missing identity"}]},
            {"data": [None]},
        ],
    )
    async def test_invalid_snapshot_raises_and_preserves_caches(
        self, body: object
    ) -> None:
        peer = SnapshotPeer(body)
        async with peer.context() as ctx:
            tools = AgentTools.from_context(ctx)
            tools_before = tools.participants
            context_before = ctx.participants
            with pytest.raises((ValueError, ParsingError)):
                await tools.get_participants_snapshot()
            assert tools.participants == tools_before
            assert ctx.participants == context_before

    async def test_failed_snapshot_raises_instead_of_returning_empty(self) -> None:
        peer = SnapshotPeer({"error": "membership unavailable"}, status=422)
        async with peer.context() as ctx:
            with pytest.raises(ApiError):
                await AgentTools.from_context(ctx).get_participants_snapshot()

    async def test_fake_projects_seeded_membership_without_adding_senders(self) -> None:
        tools = FakeAgentTools(participants=[AGENT])
        roster = await tools.get_participants_snapshot()
        assert [p.id for p in roster] == [AGENT["id"]]
        roster.clear()
        assert [p.id for p in await tools.get_participants_snapshot()] == [AGENT["id"]]

    @pytest.mark.parametrize(
        "participants", [[], [ALICE, ALICE], [{**ALICE, "id": ""}]]
    )
    async def test_fake_rejects_non_authoritative_seed(
        self, participants: list[dict[str, str]]
    ) -> None:
        with pytest.raises(ValueError):
            await FakeAgentTools(participants=participants).get_participants_snapshot()
