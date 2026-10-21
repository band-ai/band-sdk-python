"""Configured singleton retry requires confirmation and real readmission."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.settlement import SettlementResult
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.continuation import running_failure_continuation
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@lane(Lane.CORE)
@pytest.mark.asyncio(loop_scope="session")
class TestFailureContinuation:
    async def test_confirmed_failure_permits_a_successful_retry(
        self,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        baseline_settings: BaselineSettings,
        reply_capture: CaptureFactory,
    ) -> None:
        agent = await resource_manager.provision_agent("failure-retry")
        room_id = await resource_manager.provision_room(participants=[agent.id])
        async with (
            reply_capture(room_id) as capture,
            running_failure_continuation(
                agent, room_id, baseline_settings, user_ops
            ) as control,
        ):
            mid = await user_ops.send_message(
                room_id,
                "Retry this request.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await control.wait_for_failure(deadline_s=baseline_settings.e2e_timeout)
            assert control.failure_result is SettlementResult.ACCEPTED
            assert control.retained_failure
            assert await capture.durable_delivery_statuses([mid], agent.id) == {
                mid: DeliveryStatus.FAILED
            }

            control.release_confirmation.set()
            await capture.wait_for_processed(mid, agent.id)

            assert control.retry_admitted_after_confirmation
            assert control.retry_executed_after_confirmation
