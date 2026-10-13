"""A running ACP agent removed from a room and re-added there answers again.

The Band agent keeps running across the removal; a reply carrying the second
turn's own marker shows the rejoined room is served.
"""

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
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# One barrier before leaving, one after rejoining (which also boots a new process).
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)


@per_adapter(
    Adapter.COPILOT_ACP,
    Adapter.CURSOR_ACP,
    Adapter.OMP_ACP,
    prompt=LIVENESS_REPLY_PROMPT,
)
@pytest.mark.timeout(extra=BUDGET.extra_s)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_agent_replies_after_rejoining_a_room(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    room_id = await resource_manager.provision_room(
        title=f"e2e-acp-rejoin-{agent.adapter_id}", participants=[agent.id]
    )
    before, after = unique_marker("before"), unique_marker("after")

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            liveness_probe(before),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(
            mid, agent.id, deadline_s=BUDGET.deadline_s
        )
        replies.assert_contains_any([before])

        await user_ops.remove_participant(room_id, agent.id)
        await user_ops.add_participant(room_id, agent.id)

        mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id,
            liveness_probe(after),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(
            mid, agent.id, since=mark, deadline_s=BUDGET.deadline_s
        )
        replies.assert_contains_any([after])
