"""A one-shot control acknowledges only itself and a rejection never drains."""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker, OneShotStatus
from tests.runtime.deferral.kit import ApprovalAdapter, envelope
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
)


class TestOneshotControl:
    async def test_oneshot_control_only_acknowledges_itself_and_rejection_never_drains(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("B")
        peer.add_message("C")
        peer.messages[-1]["content"] = "approve"
        adapter = ApprovalAdapter()
        adapter.reject = True
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
            await invoker.startup()
            result = await invoker.handle_event(envelope("B"))
            assert result["status"] == OneShotStatus.NOT_ACCEPTED
            assert peer.marked("processed") == peer.marked("failed") == []
            result = await invoker.handle_event(envelope("C"))
            assert result["status"] == OneShotStatus.DONE
            assert peer.marked("processed") == ["C"]
            assert "drained" not in result
            assert adapter.attempted == ["B"]
            # Neither a duplicate control nor the old ordinary snapshot can drain B.
            await invoker.handle_event(envelope("C"))
            assert adapter.controls == ["C"]
            assert [message["id"] for message in peer.messages] == ["B"]
