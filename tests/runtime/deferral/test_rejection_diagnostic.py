"""An input the adapter did not accept says why it stays pending."""

from __future__ import annotations

import logging

import pytest

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
from tests.runtime.scheduler.driver import attempt_delivery


class TestRejectionDiagnostic:
    @pytest.mark.parametrize("hosting", ["live", "recovery"])
    async def test_unaccepted_work_logs_why_it_remains_pending(
        self, hosting: str, caplog: pytest.LogCaptureFixture
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
            with caplog.at_level(logging.DEBUG, logger="band.runtime.execution"):
                if hosting == "live":
                    await attempt_delivery(
                        ctx, make_message_event(room_id=ROOM_ID, msg_id="B")
                    )
                else:
                    await ctx.scheduler.run_pass(recovery=True)
            await ctx.stop()

        assert peer.marked("processed") == peer.marked("failed") == []
        rejection = next(
            (record for record in caplog.records if "not accepted" in record.message),
            None,
        )
        assert rejection is not None, "Outstanding work needs a rejection diagnostic"
        assert rejection.levelno == logging.DEBUG
