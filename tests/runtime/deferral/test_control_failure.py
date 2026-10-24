"""A control whose handler fails keeps the failed-delivery policy."""

from __future__ import annotations

from typing import Any

import pytest

from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.deferral.kit import ApprovalAdapter, envelope
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)


class TestControlFailure:
    @pytest.mark.parametrize("hosting", ["runtime", "oneshot"])
    async def test_real_control_failure_keeps_existing_failed_delivery_policy(
        self, hosting: str
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("B")
        peer.add_message("C")
        peer.messages[-1]["content"] = "approve"

        class BrokenControlAdapter(ApprovalAdapter):
            async def try_handle_control(self, msg: Any, tools: Any) -> bool:
                if msg.content == "approve":
                    raise OSError("control transport failed")
                return False

        adapter = BrokenControlAdapter()
        adapter.reject = True
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            if hosting == "runtime":
                ctx = run_through(
                    adapter,
                    link,
                    config=SessionConfig(
                        enable_context_hydration=False,
                        enable_working_state=False,
                        max_message_retries=1,
                        report_turn_failures_to_room=False,
                    ),
                )
                await ctx.scheduler.run_pass(recovery=True)
                await ctx.scheduler.run_pass(recovery=True)
                assert peer.marked("failed") == ["C"]
                await ctx.stop()
            else:
                invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
                await invoker.startup()
                with pytest.raises(OSError, match="control transport failed"):
                    await invoker.handle_event(envelope("C"))
                assert peer.marked("failed") == ["C"]
            assert peer.marked("processed") == []
            assert "C" not in adapter.attempted
