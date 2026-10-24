"""Reconnect probes preserve a stopped room when /next returns 204."""

from __future__ import annotations

from typing import Any

from band.platform.event import ReconnectedEvent
from band.runtime.execution import ExecutionContext
from tests.runtime.executioninterrupt.kit import auxiliary_context
from tests.runtime.helpers import LifecyclePlatform, rest_client_over
from tests.runtime.scheduler.driver import attempt_delivery


class TestStopSurvivesReconnect:
    async def test_reconnect_while_stopped_does_not_invoke_adapter(self):
        """Reconnect probes preserve a stopped room when /next returns 204."""
        peer = LifecyclePlatform(stopped=True)
        peer.add_message("stuck")
        executed: list[str] = []

        async def on_execute(ctx: ExecutionContext, event: Any) -> None:
            executed.append(event.payload.id)

        async with rest_client_over(peer.answer) as rest:
            ctx = auxiliary_context(rest, on_execute)
            ctx.stop_room()
            await attempt_delivery(ctx, ReconnectedEvent())
            still_stopped = ctx.is_stopped

        assert executed == []
        assert peer.requested_marks == []
        # The diagnostic processing listing is never used to dispatch execution.
        assert peer.processing_list_reads == 0
        assert len(peer.next_reads) == 1
        assert still_stopped
