from __future__ import annotations

from typing import Any

from band.platform.link import BandLink
from band.runtime.execution import (
    ExecutionContext,
)
from band.runtime.scheduler import ResyncOutcome
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.helpers import LifecyclePlatform, rest_client_over


class TestStoppedStartupUsesNextGateNotProcessingList:
    async def test_stopped_startup_uses_next_gate_not_processing_list(
        self,
    ) -> None:
        peer = LifecyclePlatform(stopped=True)
        peer.add_message("stale")
        peer.add_message("newer")
        invoked: list[str] = []

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)

        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id="agent-123", api_key="test")
            link.rest = rest
            ctx = ExecutionContext(
                "room-123",
                link,
                handler,
                config=SessionConfig(enable_context_hydration=False),
            )
            assert await ctx.scheduler.run_pass(recovery=True) is ResyncOutcome.EMPTY
            assert invoked == []
            assert peer.accepted_marks == []
            assert peer.processing_list_reads == 0
            peer.stopped = False
            assert await ctx.scheduler.run_pass(recovery=True) is ResyncOutcome.WORK
            assert invoked == ["stale", "newer"]
            assert peer.accepted_marks == [
                ("stale", "processing"),
                ("stale", "processed"),
                ("newer", "processing"),
                ("newer", "processed"),
            ]
