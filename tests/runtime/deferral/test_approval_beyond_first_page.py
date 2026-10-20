from __future__ import annotations

import asyncio

import pytest

from band.platform.event import ReconnectedEvent
from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.conftest import wait_for_condition
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)


class TestApprovalBeyondFirstPage:
    @pytest.mark.parametrize("recovery", ["live", "missed", "reconnect", "startup"])
    async def test_approval_beyond_first_page_releases_ordered_work(
        self, recovery: str
    ) -> None:
        peer = LifecyclePlatform(page_size=3)
        deferred = [f"D{i}" for i in range(7)]
        # The rest are reachable only through the paginated /next scan.
        live_deferred = deferred[:3]
        peer.add_message("A")
        adapter = ApprovalAdapter()
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(
                adapter,
                link,
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                    idle_resync_seconds=0.01,
                    idle_resync_max_seconds=0.01,
                    max_message_retries=1,
                ),
            )
            if recovery == "startup":
                peer.add_message("B")
            await ctx.start()
            traffic = asyncio.Event()

            async def ordinary_traffic() -> None:
                while not traffic.is_set():
                    await ctx.on_event(
                        make_message_event(room_id=ROOM_ID, msg_id=deferred[0])
                    )
                    await asyncio.sleep(0.001)

            traffic_task: asyncio.Task[None] | None = None
            try:
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                original_tools = adapter.active_tools
                if recovery != "startup":
                    peer.add_message("B")
                await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="B"))
                await wait_for_condition(lambda: "B" in adapter.attempted)
                assert peer.marked("processed") == ["A"]
                assert peer.marked("failed") == []
                # More than one chronological page hides a missed approval from /next.
                for message_id in deferred:
                    peer.add_message(message_id)
                peer.add_message("C")
                peer.messages[-1]["content"] = "approve"
                if recovery == "live":
                    await ctx.on_event(
                        make_message_event(
                            room_id=ROOM_ID, msg_id="C", content="approve"
                        )
                    )
                elif recovery == "reconnect":
                    await ctx.on_event(ReconnectedEvent())
                # Sustained ordinary delivery cannot rewind an in-progress scan.
                traffic_task = asyncio.create_task(ordinary_traffic())
                for message_id in live_deferred:
                    await ctx.on_event(
                        make_message_event(room_id=ROOM_ID, msg_id=message_id)
                    )
                await wait_for_condition(
                    lambda: deferred[-1] in peer.marked("processed"), timeout=3
                )
                assert original_tools.turn.complete
                assert adapter.completed == ["A", "B", *deferred]
                assert adapter.controls == ["C"]
                assert peer.marked("processed") == [
                    "A",
                    "C",
                    "B",
                    *deferred,
                ]
                assert peer.marked("failed") == []
                assert any(cursor is not None for cursor in peer.actionable_cursors)
                # A terminal duplicate cannot reopen or reapply the approval.
                await ctx.on_event(
                    make_message_event(room_id=ROOM_ID, msg_id="C", content="approve")
                )
                await asyncio.sleep(0)
                assert adapter.controls == ["C"]
            finally:
                traffic.set()
                if traffic_task is not None:
                    await traffic_task
                await ctx.stop()
