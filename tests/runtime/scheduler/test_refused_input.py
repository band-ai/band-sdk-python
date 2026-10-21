"""A host that refuses input is offered it once per pass, then again on the next resync."""

from __future__ import annotations

import pytest

from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestRefusedInput:
    async def test_refusal_is_not_retried_inside_one_pass(self) -> None:
        peer = RestPeer()
        async with started_session(peer, accepting=False) as session:
            await session.quiesce()
            event = peer.post("busy")

            await session.deliver(event)
            await session.quiesce()
            offered_in_first_pass = list(session.turns.refused)

            session.turns.accepting = True
            await session.resync()
            await session.settled("busy")

        assert offered_in_first_pass == [("busy",)]
        assert session.turns.ids == [("busy",)]
