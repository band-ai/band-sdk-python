"""A contact-hub turn the host refused keeps its place ahead of later backlog."""

from __future__ import annotations

import pytest

from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import synthetic_message

pytestmark = pytest.mark.looptime


class TestRefusedSynthetic:
    async def test_refused_hub_turn_runs_before_a_later_message(self) -> None:
        peer = RestPeer()
        async with started_session(peer, accepting=False) as session:
            await session.quiesce()
            await session.deliver(synthetic_message("hub-turn"))
            await session.quiesce()
            session.turns.accepting = True
            await session.deliver(peer.post("later", at=5))
            await session.quiesce()
            await session.resync()
            await session.settled("later")

        assert session.turns.ids == [("hub-turn",), ("later",)]
