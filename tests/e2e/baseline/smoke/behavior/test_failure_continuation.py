"""An unknown FAILED response gates real singleton execution retries."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.delivery import (
    RecoveringAdapter,
    lose_failed_response,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_agent_with_handle,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.asyncio(loop_scope="session")


class TestFailureContinuation:
    @lane(Lane.CORE)
    async def test_a_failed_original_retries_only_after_its_write_is_confirmed(
        self,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_settings: BaselineSettings,
        invocation_observer: InvocationObserver,
    ) -> None:
        identity = await resource_manager.provision_agent("failed-retry")
        adapter = RecoveringAdapter()
        invocation_observer.watch(adapter)
        async with running_agent_with_handle(
            identity,
            adapter,
            baseline_settings,
            session_config=SessionConfig(
                max_message_retries=2,
                idle_resync_seconds=1,
                idle_resync_max_seconds=1,
                enable_working_state=False,
                enable_context_hydration=False,
            ),
        ) as agent:
            room_id = await resource_manager.provision_room(participants=[identity.id])
            with lose_failed_response(
                agent.runtime.link, deadline_s=baseline_settings.e2e_timeout
            ) as fault:
                async with reply_capture(room_id) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        "Exercise the configured failure retry.",
                        mention_id=identity.id,
                        mention_name=identity.name,
                    )
                    await capture.wait_for_delivery(
                        mid, identity.id, until={DeliveryStatus.FAILED}
                    )
                    await fault.wait_for_retry()
                    assert invocation_observer.partition(room_id) == [(mid,)]

                    fault.release()
                    await capture.wait_for_processed(mid, identity.id)

                assert invocation_observer.partition(room_id) == [(mid,), (mid,)]
