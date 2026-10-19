"""Unconsumed context does not turn a setup failure into scheduler deferral."""

from __future__ import annotations

import pytest

from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.preprocessing.handoffkit import HandoffAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    PostingPlatform,
    rest_client_over,
    run_through,
)
from tests.runtime.oneshot.kit import msg_body


class TestSetupFailureAccounting:
    @pytest.mark.parametrize("hosting", ["live", "recovery"])
    async def test_setup_failure_spends_normal_retry_budget(self, hosting: str) -> None:
        peer = PostingPlatform()
        peer.add_message("B")
        error = OSError("setup dependency unavailable")
        adapter = HandoffAdapter(error=error)
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
            try:
                if hosting == "live":
                    await ctx._process_event(
                        make_message_event(room_id=ROOM_ID, msg_id="B")
                    )
                else:
                    await ctx._reconcile()
                for _ in range(4):
                    await ctx._reconcile()
                assert peer.marked("failed") == ["B", "B"]
                assert peer.marked("processed") == []
                assert len(adapter.received) == 2
                assert all(snapshot.bootstrap for snapshot in adapter.received)
            finally:
                await ctx.stop()

    async def test_oneshot_preserves_original_setup_failure_and_marks_failed(
        self,
    ) -> None:
        peer = PostingPlatform()
        peer.add_message("B")
        error = OSError("setup dependency unavailable")
        adapter = HandoffAdapter(error=error)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
            await invoker.startup()
            try:
                with pytest.raises(OSError) as failure:
                    await invoker.handle_event(
                        msg_body(msg_id="B", room_id=ROOM_ID, agent_id=AGENT_ID)
                    )
            finally:
                await invoker.shutdown()
        assert failure.value is error
        assert peer.marked("failed") == ["B"]
        assert peer.marked("processed") == []
