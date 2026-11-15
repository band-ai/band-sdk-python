"""A host that refuses input is offered it once per pass, then again on the next resync."""

from __future__ import annotations

import pytest

from tests.runtime.delivery.kit import JournaledTurns, RestPeer, room_over

pytestmark = pytest.mark.looptime


class TestRefusedInput:
    async def test_refusal_is_not_retried_inside_one_pass(self) -> None:
        peer = RestPeer()
        turns = JournaledTurns(accepting=False, journal=peer.journal)
        peer.post("busy")
        async with room_over(peer, turns.single) as ctx:
            await ctx.scheduler.run_pass(recovery=True)
            offered_in_first_pass = list(turns.refused)

            turns.accepting = True
            await ctx.scheduler.run_pass(recovery=True)

        assert offered_in_first_pass == [("busy",)]
        assert turns.ids == [("busy",)]
