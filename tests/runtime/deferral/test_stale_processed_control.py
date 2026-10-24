"""A control the platform already processed cannot be reopened by a one-shot."""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker, OneShotStatus
from tests.runtime.deferral.kit import ApprovalAdapter, envelope
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
)


class TestStaleProcessedControl:
    async def test_stale_processed_control_cannot_be_reopened_by_oneshot(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("C")
        peer.messages[-1]["metadata"] = {
            "delivery_status": {AGENT_ID: {"status": "processed"}}
        }
        adapter = ApprovalAdapter()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
            await invoker.startup()
            result = await invoker.handle_event(envelope("C"))
            assert result["status"] == OneShotStatus.ALREADY_PROCESSED
            assert adapter.controls == adapter.attempted == []
            assert peer.requested_marks == []
