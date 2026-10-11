"""Recover a missed message push while the real socket remains connected."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.control import ControlRuntime, running_control_runtime
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.asyncio(loop_scope="session")


@lane(Lane.CORE)
async def test_missed_message_push_recovers_through_rest(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    baseline_settings: BaselineSettings,
    reply_capture: CaptureFactory,
) -> None:
    agent = await resource_manager.provision_agent("missedpush")
    room_id = await resource_manager.provision_room(participants=[agent.id])
    marker = uuid4().hex
    content = f"recover missed push {marker}"
    control = ControlRuntime(block_cycles=0, withheld_message_marker=marker)
    async with (
        reply_capture(room_id) as capture,
        running_control_runtime(
            agent, room_id, baseline_settings, user_ops, control=control
        ),
    ):
        await control.wait_for_startup_sync(deadline_s=baseline_settings.e2e_timeout)
        mid = await user_ops.send_message(
            room_id, content, mention_id=agent.id, mention_name=agent.name
        )
        await control.wait_for_withheld_message(
            deadline_s=baseline_settings.e2e_timeout
        )
        assert mid in control.withheld_message_ids
        await capture.wait_for_processed(mid, agent.id)
        assert control.invoked_message_ids == [mid]
        assert control.completed_message_ids == [mid]
