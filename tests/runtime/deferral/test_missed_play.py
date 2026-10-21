"""A missed PLAY releases busy work at the existing reconciliation boundary."""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)


class TestMissedPlay:
    async def test_missed_play_releases_busy_work_at_existing_reconciliation_boundary(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("B")
        adapter = ApprovalAdapter()
        adapter.reject = True
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False, enable_working_state=False
                ),
            )
            await ctx.scheduler.run_pass(recovery=True)
            ctx.stop_room()
            peer.stopped = True
            await ctx.scheduler.run_pass(recovery=True)
            assert peer.marked("processed") == []
            # PLAY was lost on the socket; /next's accepted gate discovers it.
            peer.stopped = False
            adapter.reject = False
            peer.add_message("D")
            await ctx.scheduler.run_pass(recovery=True)
            assert adapter.completed == ["B", "D"]
            assert peer.marked("processed") == ["B", "D"]
            assert peer.marked("failed") == []
            await ctx.stop()
