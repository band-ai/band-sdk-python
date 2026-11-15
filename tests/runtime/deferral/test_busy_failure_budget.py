"""Busy attempts never spend the previous execution failure's budget."""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.delivery import TurnOutcome
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.deferral.kit import FailureThenBusyAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestBusyFailureBudget:
    async def test_busy_attempts_preserve_previous_execution_failure_budget(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("B")
        adapter = FailureThenBusyAdapter()

        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_message_retries=2,
                    report_turn_failures_to_room=False,
                ),
            )
            event = make_message_event(room_id=ROOM_ID, msg_id="B")
            try:
                assert await attempt_delivery(ctx, event) is TurnOutcome.NOT_ACCEPTED
                for _ in range(4):
                    await ctx.scheduler.run_pass(recovery=True)
                assert peer.marked("failed") == ["B"]
                assert peer.marked("processed") == []
                adapter.busy = False
                await ctx.scheduler.run_pass(recovery=True)
                await ctx.scheduler.run_pass(recovery=True)
                assert peer.marked("failed") == ["B", "B"]
                assert (adapter.failure_attempts, adapter.busy_attempts) == (2, 5)
            finally:
                await ctx.stop()
