"""Busy attempts never spend the previous execution failure's budget."""

from __future__ import annotations

from typing import Any

from band.core.exceptions import InputNotAccepted
from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)


class TestBusyFailureBudget:
    async def test_busy_attempts_preserve_previous_execution_failure_budget(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("B")
        mode = "failure"
        invoked: list[str] = []

        class FailingAdapter(ApprovalAdapter):
            async def on_message(
                self, msg: Any, tools: Any, *args: Any, **kwargs: Any
            ) -> None:
                invoked.append(mode)
                if mode == "busy":
                    raise InputNotAccepted()
                raise OSError("provider transport uncertainty")

        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                FailingAdapter(),
                link,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    max_message_retries=2,
                    report_turn_failures_to_room=False,
                ),
            )
            event = make_message_event(room_id=ROOM_ID, msg_id="B")
            await ctx._process_event(event)
            mode = "busy"
            for _ in range(5):
                await ctx._reconcile()
            assert peer.marked("failed") == ["B"]
            assert peer.marked("processed") == []
            mode = "failure"
            await ctx._reconcile()
            await ctx._reconcile()
            assert peer.marked("failed") == ["B", "B"]
            assert invoked == ["failure", *["busy"] * 5, "failure"]
            await ctx.stop()
