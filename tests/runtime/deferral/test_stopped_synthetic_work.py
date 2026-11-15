"""A stop during synthetic execution keeps the work for PLAY."""

from __future__ import annotations

import asyncio
from typing import Any

from band.platform.link import BandLink
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.conftest import wait_for_condition
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
    synthetic_message,
)


class TestStoppedSyntheticWork:
    async def test_stop_during_synthetic_execution_retains_work_for_play(self) -> None:
        peer = LifecyclePlatform()
        entered = asyncio.Event()
        blocker = asyncio.Event()

        class InterruptibleAdapter(ApprovalAdapter):
            async def on_message(
                self, msg: Any, tools: Any, *args: Any, **kwargs: Any
            ) -> None:
                if not entered.is_set():
                    entered.set()
                    await blocker.wait()
                await super().on_message(msg, tools, *args, **kwargs)

        adapter = InterruptibleAdapter()
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
                ),
            )
            await ctx.start()
            try:
                await ctx.on_event(synthetic_message("local-B"))
                await entered.wait()
                peer.stopped = True
                ctx.stop_room()
                await wait_for_condition(lambda: ctx._active_cycle_task is None)
                assert adapter.completed == []
                peer.stopped = False
                await ctx.resume_room()
                await wait_for_condition(lambda: adapter.completed == ["local-B"])
                assert peer.requested_marks == []
            finally:
                await ctx.stop()
