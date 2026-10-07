"""Live INTERRUPT cancellation and consumption with a deterministic handler."""

from __future__ import annotations

import pytest

from band.client.streaming import ControlMode, DeliveryStatus
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.control import running_control_runtime
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.asyncio(loop_scope="session")


@lane(Lane.CORE)
@flaky_infra(
    "agent.control is platform-documented best-effort delivery (see "
    "ThenvoiCom.Channels.AgentControl moduledoc); a dropped push times out "
    "control.wait_for_cancellation with no code defect on either side"
)
async def test_interrupt_cancels_and_consumes(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """INTERRUPT cancels an active cycle and consumes its message."""
    agent = await resource_manager.provision_agent("interrupt")
    room_id = await resource_manager.provision_room(participants=[agent.id])

    async with (
        running_control_runtime(agent, room_id, baseline_settings, user_ops) as control,
        reply_capture(room_id) as capture,
    ):
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
        await control.wait_for_cancellation(deadline_s=baseline_settings.e2e_timeout)
        assert ControlMode.INTERRUPT in control.received_control_modes
        await capture.wait_for_processed(mid, agent.id)

    assert mid not in control.completed_message_ids, (
        "INTERRUPT replayed or completed the cancelled message"
    )
