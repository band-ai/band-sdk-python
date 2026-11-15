"""A native agent serves a room again after its leave cleanup completes."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    LIVENESS_REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.lifecycle import observe_room_cleanup
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Two replies and confirmed leave cleanup each have a bounded barrier.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=3)


class TestRoomRejoin:
    @per_adapter(
        Adapter.CLAUDE_SDK,
        Adapter.CODEX,
        Adapter.COPILOT_ACP,
        Adapter.CURSOR_ACP,
        Adapter.OMP_ACP,
        Adapter.OMP,
        prompt=LIVENESS_REPLY_PROMPT,
    )
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_agent_replies_after_confirmed_leave(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        identity = await cell.provision()
        room_id = await cell.resources.provision_room(
            title=f"e2e-rejoin-{cell.adapter_id}", participants=[identity.id]
        )
        adapter = cell.build()
        before, after = unique_marker("before"), unique_marker("after")

        with (
            observe_room_cleanup(adapter, room_id) as cleanup,
            cell.resources.track_running(identity.id),
        ):
            async with (
                running_agent(identity, adapter, cell.settings),
                reply_capture(room_id) as capture,
            ):
                mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(before),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, deadline_s=BUDGET.deadline_s
                )
                replies.assert_contains_any([before])

                cleanup.completed.clear()
                await user_ops.remove_participant(room_id, identity.id)
                await cleanup.wait(deadline_s=BUDGET.deadline_s)
                await user_ops.add_participant(room_id, identity.id)

                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(after),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, since=mark, deadline_s=BUDGET.deadline_s
                )
                replies.assert_contains_any([after])
