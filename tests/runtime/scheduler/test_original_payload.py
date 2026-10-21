"""An original whose admission was refused reaches the host as the socket sent it."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus, MessageMetadata
from tests.conftest import make_message_event
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import AGENT_ID, ROOM_ID, SENDER_ID

pytestmark = pytest.mark.looptime


class TestOriginalPayload:
    async def test_a_refused_admission_keeps_the_socket_metadata(self) -> None:
        peer = RestPeer()
        # The socket mentions carry usernames; REST stores display names.
        event = make_message_event(
            room_id=ROOM_ID,
            msg_id="original",
            content="original",
            sender_id=SENDER_ID,
            metadata=MessageMetadata(
                status="sent", mentions=[{"id": AGENT_ID, "username": "agent-handle"}]
            ),
        )
        expected = event.payload.metadata.model_dump()
        async with started_session(peer) as session:
            await session.quiesce()
            peer.post("original")
            peer.refuse_processing.add("original")
            await session.deliver(event)
            await session.until(lambda: bool(peer.writes_of(DeliveryStatus.PROCESSING)))
            peer.refuse_processing.clear()
            await session.resync()
            await session.settled("original")

        assert [p.metadata.model_dump() for p in session.turns.offered] == [expected]
        assert event.payload.metadata.model_dump() == expected
