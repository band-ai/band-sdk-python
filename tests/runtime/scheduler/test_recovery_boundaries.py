"""Recovery respects local event boundaries despite older REST timestamps."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.event import ParticipantAddedEvent, PlatformEvent, ReconnectedEvent
from tests.conftest import make_participant_added_event
from tests.runtime.batching.kit import Turns
from tests.runtime.conftest import Waiting
from tests.runtime.delivery.kit import RestPeer, room_over, started_session
from tests.runtime.helpers import ROOM_ID, synthetic_message

LOCAL = "local"


@pytest.mark.parametrize(
    ("boundary", "marker"),
    [
        pytest.param(
            make_participant_added_event(
                room_id=ROOM_ID, participant_id="new", name="New", type="User"
            ),
            ParticipantAddedEvent.type,
            id="roster",
        ),
        pytest.param(synthetic_message(LOCAL), LOCAL, id="synthetic"),
    ],
)
class TestRecoveryBoundaries:
    @pytest.mark.parametrize("after_at", [1, 2, 3], ids=["older", "same-time", "newer"])
    async def test_reconnect_keeps_messages_on_their_side_of_a_local_boundary(
        self, boundary: PlatformEvent, marker: str, after_at: int
    ) -> None:
        peer = RestPeer()
        peer.post("warmup", at=0)
        async with started_session(peer, gate=True) as session:
            await session.until(lambda: session.turns.ids == [("warmup",)])
            await session.deliver(
                peer.post("before", at=2),
                boundary,
                peer.post("after", at=after_at),
                ReconnectedEvent(room_id=ROOM_ID),
            )
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("before", "after")
            await session.until(lambda: len(session.turns.ids) == 4)

        assert session.turns.ids == [
            ("warmup",),
            ("before",),
            (marker,),
            ("after",),
        ]

    async def test_startup_respects_a_boundary_before_the_first_socket_handoff(
        self, boundary: PlatformEvent, marker: str
    ) -> None:
        peer = RestPeer()
        turns = Turns()
        async with room_over(peer, turns.single) as ctx:
            await ctx.on_event(peer.post("before", at=2))
            await ctx.on_event(boundary)
            await ctx.on_event(peer.post("after", at=1))
            await ctx.start()
            await Waiting.until(lambda: len(turns.ids) == 3)

        assert turns.ids == [("before",), (marker,), ("after",)]
        assert peer.status_of("after") is DeliveryStatus.PROCESSED

    async def test_a_later_held_claim_cannot_block_its_local_predecessors(
        self, boundary: PlatformEvent, marker: str
    ) -> None:
        peer = RestPeer()
        peer.post("warmup", at=0)
        async with started_session(peer, gate=True) as session:
            await session.until(lambda: session.turns.ids == [("warmup",)])
            assert session.ctx.claims.try_claim(ROOM_ID, "after")
            await session.deliver(
                peer.post("before", at=2),
                boundary,
                peer.post("after", at=1),
                ReconnectedEvent(room_id=ROOM_ID),
            )
            session.turns.gate = False
            session.turns.release.set()
            await session.until(lambda: len(session.turns.ids) == 3)
            assert session.turns.ids == [("warmup",), ("before",), (marker,)]
            assert peer.status_of("before") is DeliveryStatus.PROCESSED

            session.ctx.claims.release(ROOM_ID, "after")
            await session.ctx.request_resync()
            await session.settled("after")

        assert session.turns.ids == [
            ("warmup",),
            ("before",),
            (marker,),
            ("after",),
        ]

    async def test_a_predecessor_keeps_its_payload_and_the_pages_fresh_status(
        self, boundary: PlatformEvent, marker: str
    ) -> None:
        peer = RestPeer()
        peer.post("warmup", at=0)
        async with started_session(peer, gate=True) as session:
            await session.until(lambda: session.turns.ids == [("warmup",)])
            peer.post("finished", at=1)
            session.ctx.claims.remember_completed(ROOM_ID, "finished")
            peer.post("before", at=3, status=DeliveryStatus.PROCESSED)
            original = peer.replay("before")
            assert original.payload is not None
            expected = original.payload.model_dump()
            peer.set_status("before", DeliveryStatus.PROCESSING)
            await session.deliver(
                original,
                boundary,
                peer.post("after", at=2),
                ReconnectedEvent(room_id=ROOM_ID),
            )
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("before", "after")
            await session.until(lambda: len(session.turns.ids) == 4)

        assert session.turns.ids == [
            ("warmup",),
            ("before",),
            (marker,),
            ("after",),
        ]
        delivered = next(p for p in session.turns.offered if p.id == "before")
        assert delivered.model_dump() == expected
