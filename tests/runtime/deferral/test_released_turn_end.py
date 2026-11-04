"""Input refused while a released turn runs is retried once that turn ends."""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.conftest import wait_for_condition
from tests.runtime.deferral.kit import EndsWhileRefusingAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    NO_PERIODIC_WAKE_S,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)


class TestReleasedTurnEnd:
    async def test_input_refused_as_the_turn_ends_is_retried_without_a_periodic_pass(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        adapter = EndsWhileRefusingAdapter()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    idle_resync_seconds=NO_PERIODIC_WAKE_S,
                    idle_resync_max_seconds=NO_PERIODIC_WAKE_S,
                ),
            )
            await ctx.start()
            try:
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                # The released turn ends before the room records B's refusal.
                peer.add_message("B")
                await ctx.on_event(peer.event("B"))

                await wait_for_condition(lambda: peer.marked("processed") == ["A", "B"])
                assert adapter.completed == ["A", "B"]
                assert peer.marked("failed") == []
            finally:
                await ctx.stop()
