"""A stop between control discovery and its claim prevents provider effects."""

from __future__ import annotations

from typing import Any

from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)


class TestStopBeforeClaim:
    async def test_stop_between_control_discovery_and_claim_prevents_provider_effects(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("B")
        peer.add_message("C")
        peer.messages[-1]["content"] = "approve"
        adapter = ApprovalAdapter()
        adapter.reject = True

        def stopped_control_gate(request: Any) -> Any:
            response = peer.answer(request)
            if request.url.path.endswith("/messages"):
                peer.stopped = True
            return response

        async with rest_client_over(stopped_control_gate) as rest:
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
            assert adapter.controls == []
            assert peer.requested("processing") == ["B", "C"]
            assert peer.marked("processing") == ["B"]
            assert peer.marked("processed") == peer.marked("failed") == []
            await ctx.stop()
