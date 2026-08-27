"""Live STOP -> PLAY control handling against the platform.

This uses a deterministic handler rather than an LLM: it proves the control
push reaches the runtime, cancels the active turn, and resumes it from /next.
"""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.control import running_control_runtime
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.asyncio(loop_scope="session")


@lane(Lane.CORE)
async def test_stop_cancels_then_play_replays(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """STOP cancels the active cycle; PLAY replays that same message."""
    agent = await resource_manager.provision_agent("control")
    room_id = await resource_manager.provision_room(participants=[agent.id])

    async with running_control_runtime(
        agent, room_id, baseline_settings, user_ops
    ) as control:
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "Run until stopped.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_delivery(
                mid, agent.id, until={DeliveryStatus.PROCESSING}
            )
            await control.wait_for_start(deadline_s=baseline_settings.e2e_timeout)

            await user_ops.stop_agent(room_id)
            await control.wait_for_cancellation(
                deadline_s=baseline_settings.e2e_timeout
            )
            assert mid not in control.completed_message_ids

            await user_ops.play_agent(room_id)
            await capture.wait_for_processed(mid, agent.id)

    assert mid in control.completed_message_ids, (
        "PLAY did not replay the stopped message"
    )


@lane(Lane.CORE)
async def test_interrupt_cancels_and_consumes(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """INTERRUPT cancels an active cycle and consumes its message."""
    agent = await resource_manager.provision_agent("interrupt")
    room_id = await resource_manager.provision_room(participants=[agent.id])

    async with running_control_runtime(
        agent, room_id, baseline_settings, user_ops
    ) as control:
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "Run until interrupted.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_delivery(
                mid, agent.id, until={DeliveryStatus.PROCESSING}
            )
            await control.wait_for_start(deadline_s=baseline_settings.e2e_timeout)

            await user_ops.interrupt_active_agent_execution(agent.id)
            await control.wait_for_cancellation(
                deadline_s=baseline_settings.e2e_timeout
            )
            await capture.wait_for_processed(mid, agent.id)

    assert mid not in control.completed_message_ids, (
        "INTERRUPT replayed or completed the cancelled message"
    )
