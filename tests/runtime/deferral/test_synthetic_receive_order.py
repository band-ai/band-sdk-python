"""Synthetic work survives a stop and a rejection, in receive order."""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
    synthetic_message,
)


class TestSyntheticReceiveOrder:
    async def test_synthetic_work_survives_stop_and_rejection_in_receive_order(
        self,
    ) -> None:
        peer = LifecyclePlatform(stopped=True)
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
            ctx.stop_room()
            for message_id in ["local-B", "local-D"]:
                await ctx.on_event(synthetic_message(message_id))
            assert adapter.completed == []
            peer.stopped = False
            await ctx.resume_room()
            await ctx.scheduler.run_pass(recovery=True)
            assert adapter.completed == []
            adapter.reject = False
            await ctx.scheduler.run_pass(recovery=True)
            assert adapter.completed == ["local-B", "local-D"]
            assert peer.requested_marks == []
            await ctx.stop()
